"""
analyze_deal.py — 单 deal / 多 deal 评估的主入口

用法:
    # 单 deal 用最近 panel snapshot (默认中位价)
    python scripts/analyze_deal.py --stock-code 1187.HK

    # 区间扫描 (low / mid / high)
    python scripts/analyze_deal.py --stock-code 1187.HK --price-scan

    # 引用历史 panel (复盘场景)
    python scripts/analyze_deal.py --stock-code 1187.HK \
        --panel-id PANEL_2025-08-15_a3f2c1

    # 多 deal 横向对比
    python scripts/analyze_deal.py --stock-codes "1187.HK,2493.HK,3296.HK" --compare

    # 落盘到 nacs_predictions (audit trail; 没传不落)
    python scripts/analyze_deal.py --stock-code 1187.HK --price-scan --persist
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))            # for run_v7_backtest
sys.path.insert(0, str(_ROOT / "src"))    # for nacs_model / data

from data.dao import db_connect
from data.panel_snapshot import (
    get_latest_panel_snapshot,
    get_panel_snapshot,
    write_panel_snapshot,
)
from data.predictions import persist_prediction


# =============================================================================
# 单 deal 三场景评估
# =============================================================================

def _ensure_panel_snapshot(conn, asof: date,
                           panel_id: Optional[str]) -> Optional[Dict]:
    """读 panel snapshot; 没传就用最近的; 仍没有就动态写一个 (stub)."""
    if panel_id:
        snap = get_panel_snapshot(conn, panel_id)
        if not snap:
            print(f"❌ panel snapshot 不存在: {panel_id}", file=sys.stderr)
            sys.exit(1)
        return snap
    snap = get_latest_panel_snapshot(conn)
    if snap:
        return snap
    print("⚠ 没有 panel_snapshots 行; 创建一个 stub (建议先跑 run_v7_backtest 形成正式快照)",
          file=sys.stderr)
    sid = write_panel_snapshot(
        conn, asof=asof,
        market_env={}, regime_score=None,
        config_dict={"version": "stub"},
        notes="stub created by analyze_deal due to no panel snapshot",
    )
    return get_panel_snapshot(conn, sid)


def _resolve_asof_for_deal(row, override_asof: Optional[str]) -> date:
    if override_asof:
        return date.fromisoformat(override_asof)
    pd_val = row["pricing_date"]
    if pd_val:
        return pd_val if isinstance(pd_val, date) else \
               date.fromisoformat(str(pd_val)[:10])
    # prospectus 阶段没 pricing_date — 用 today
    return date.today()


def _scenario_prices(row) -> List[Tuple[str, float]]:
    """从 ipo_master 拿 (low, mid, high) 价格三联. 缺时只返回 'mid'.

    'final' 场景: 已 listed 且有 offer_price_hkd → 用真实定价.
    """
    status = row["status"]
    final_price = row["offer_price_hkd"]
    low = row["offer_price_low"]
    high = row["offer_price_high"]

    out: List[Tuple[str, float]] = []
    if status in ("listed", "delisted") and final_price is not None:
        out.append(("final", float(final_price)))
        return out
    if low is not None and high is not None and high >= low > 0:
        out.append(("low", float(low)))
        out.append(("mid", round((low + high) / 2.0, 4)))
        out.append(("high", float(high)))
    elif final_price is not None:
        out.append(("mid", float(final_price)))
    else:
        # 兜底: 没价 → 1 HKD 占位 (会让 PE 计算有问题, 报告会 warn)
        out.append(("mid", 1.0))
    return out


def _evaluate_deal(conn, *, stock_code: str, asof: date,
                   scenarios: List[Tuple[str, float]],
                   themes_bundle: Optional[Dict] = None,
                   ai_revenue_pct_override: Optional[float] = None,
                   panel_snap: Optional[Dict] = None,
                   ) -> List[Dict]:
    """对一只 deal 在每个 scenario 跑一次 NACS, 返回 list of result dict.

    P0.1: themes_bundle 非 None 时, classify deal → 注入 theme_heat_score
          到 IPOOffering, 让 _score_l1_6_market modifier 生效.
    P0.2: ai_revenue_pct (CLI override / deal YAML / ai_revenue_manual)
          注入 IPOOffering, 让 AI 镀金 post-adjustment 生效.
    """
    from run_v7_backtest import build_offering, hydrate_cornerstones, get_financials, derive_profitable
    from nacs_model import (
        compute_nacs, ListingChapter, CompanyType,
        OfferingStructure, IPOOffering,
    )

    # 找 ipo_id
    row = conn.execute(
        "SELECT * FROM ipo_master WHERE stock_code = ? "
        "ORDER BY listing_date DESC LIMIT 1",
        (stock_code,),
    ).fetchone()
    if not row:
        raise SystemExit(f"❌ stock_code {stock_code} 不在 ipo_master")
    ipo_id = row["ipo_id"]

    # 用 build_offering 做基础构造 (不传 regime_score, 单 deal 不做 regime gate)
    offering_base = build_offering(conn, ipo_id, regime_score=None,
                                   use_static_env=False)
    if not offering_base:
        raise SystemExit(f"❌ build_offering 返回 None for {stock_code}")

    # P0.1: classify + 注入 theme_heat_score (需在 compute_nacs 前完成)
    if themes_bundle is not None:
        from reports.themes_data import classify_deal_to_theme
        defs_data, _ = themes_bundle.get("theme_definitions") or (None, None)
        heat_data, _ = themes_bundle.get("heat_today") or (None, None)
        ipo_concepts_for_classify = [
            r[0] for r in conn.execute(
                "SELECT concept_name FROM ipo_concepts WHERE stock_code = ?",
                (stock_code,)
            )
        ]
        cr = classify_deal_to_theme(
            stock_code=stock_code,
            gics_l2=row["gics_l2"],
            ipo_concept_names=ipo_concepts_for_classify,
            company_name=row["company_name_zh"],
            theme_definitions=defs_data,
        )
        if cr.theme_id:
            offering_base.theme_id = cr.theme_id
            if heat_data and cr.theme_id in heat_data.get("themes", {}):
                offering_base.theme_heat_score = (
                    heat_data["themes"][cr.theme_id].get("heat_score")
                )

        # P3.2.A: ps_peer_median 从 panel by_theme 推传 (cascade 到 chapter / overall)
        # 让 _score_l1_1_18c 的 PS 子项有 peer 可比 (没主题命中也走 chapter, 至少不空)
        if panel_snap is not None and offering_base.offering is not None:
            import json
            from data.panel_snapshot import lookup_ps_peer_median
            try:
                aggs = json.loads(panel_snap.get("aggregates_json") or "{}")
            except (json.JSONDecodeError, TypeError):
                aggs = {}
            ps_peer, ps_source = lookup_ps_peer_median(
                aggs,
                theme_id=cr.theme_id,
                listing_chapter=row["listing_chapter"],
            )
            if ps_peer is not None:
                offering_base.offering.ps_peer_median = ps_peer
                # source 标识"用了哪一层" (theme:xxx / chapter:xxx / overall);
                # 打印让 stdout 可追溯, IC memo 后续 phase 可挂到 thesis.
                print(f"  [P/S peer] median={ps_peer:.1f}x source={ps_source}",
                      file=sys.stderr)

        # P0.2: ai_revenue_pct 三层优先 (CLI > deal YAML > ai_revenue_manual)
        # CLI/YAML 在 main() 里收集到 ai_pct_per_code, 用 ai_revenue_pct_override
        # 传入; 否则从 ai_revenue_manual lookup (跟 thesis._resolve_ai_revenue_pct 同源)
        if ai_revenue_pct_override is not None:
            offering_base.ai_revenue_pct = float(ai_revenue_pct_override)
        else:
            manual_data, _ = themes_bundle.get("ai_revenue_manual") or (None, None)
            if manual_data:
                # 跟 themes_data._canon 一致的代码规范化
                canon = stock_code.upper().strip()
                if "." in canon:
                    num, suffix = canon.split(".", 1)
                    canon = f"{num.lstrip('0') or '0'}.{suffix}"
                if canon in manual_data:
                    offering_base.ai_revenue_pct = float(manual_data[canon])

    # 找参考价 (用于 low/high 时按比例调整 pe_at_offer)
    # 优先用 final price; 否则用区间中点
    final_price = row["offer_price_hkd"]
    low = row["offer_price_low"]
    high = row["offer_price_high"]
    mid_price = (final_price if final_price is not None
                 else ((low + high) / 2.0 if low and high else None))

    results = []
    for scenario, price in scenarios:
        # final / mid (即参考价): 不动 pe_at_offer + offering_size_hkd
        # low / high: 按 price/mid_price 比例调整 pe_at_offer (offering_size_hkd 类似)
        new_off = OfferingStructure(**{
            **offering_base.offering.__dict__,
        })
        if scenario in ("low", "high") and mid_price and mid_price > 0:
            ratio = price / mid_price
            if offering_base.offering.pe_at_offer is not None:
                new_off.pe_at_offer = offering_base.offering.pe_at_offer * ratio
            if offering_base.offering.offering_size_hkd is not None:
                new_off.offering_size_hkd = offering_base.offering.offering_size_hkd * ratio
            # P1.1: mkt_cap 也按价格 ratio 缩放 (post_ipo_shares 不变, 价格变 → 总市值变)
            if offering_base.offering.mkt_cap_at_offer_hkd is not None:
                new_off.mkt_cap_at_offer_hkd = (
                    offering_base.offering.mkt_cap_at_offer_hkd * ratio
                )

        offering_scenario = IPOOffering(**{
            **offering_base.__dict__,
            "offering": new_off,
        })

        result = compute_nacs(offering_scenario)
        results.append({
            "stock_code": stock_code,
            "ipo_id": ipo_id,
            "row": row,
            "scenario": scenario,
            "price": price,
            "offering": offering_scenario,
            "result": result,
        })
    return results


# =============================================================================
# Reports
# =============================================================================

def _print_single_report(records: List[Dict], snap: Dict, asof: date) -> None:
    if not records:
        return
    row = records[0]["row"]
    print(f"\n{'='*72}")
    print(f"  Deal: {row['stock_code']} {row['company_name_zh'] or ''} "
          f"({row['status']})")
    print(f"  Chapter: {row['listing_chapter']}  GICS: {row['gics_l2']}")
    print(f"  Listing: {row['listing_date']} (expected: "
          f"{row['expected_listing_date'] or '--'})")
    print(f"  Panel: {snap['snapshot_id']} (n={snap['n_ipos_in_universe']} "
          f"listed IPOs, regime={snap.get('regime_score')})")
    print(f"  Asof: {asof}")
    print(f"{'='*72}")

    if len(records) == 1:
        rec = records[0]
        r = rec["result"]
        print(f"\n  NACS_adjusted : {r.nacs_adjusted:.4f}")
        print(f"  decision      : {r.decision}  (position={r.position_pct:.0%})")
        print(f"  Q_company     : {r.Q_company:.4f}")
        print(f"  Q_ecosystem   : {r.Q_ecosystem:.4f}")
        print(f"  R_lockup      : {r.R_lockup:.4f}")
        if r.adjustments_applied:
            print(f"\n  Adjustments: {r.adjustments_applied}")
        if r.warnings:
            print(f"\n  ⚠ Warnings:")
            for w in r.warnings:
                print(f"    - {w}")
    else:
        # price-scan: 表格输出
        cols = ["scenario", "price", "NACS", "decision", "Q_c", "Q_e", "R_l"]
        print(f"\n  {' | '.join(c.ljust(10) for c in cols)}")
        print(f"  {'-' * (len(cols) * 13)}")
        for rec in records:
            r = rec["result"]
            row_vals = [rec["scenario"], f"{rec['price']:.2f}",
                        f"{r.nacs_adjusted:.4f}", r.decision,
                        f"{r.Q_company:.3f}", f"{r.Q_ecosystem:.3f}",
                        f"{r.R_lockup:.3f}"]
            print(f"  {' | '.join(v.ljust(10) for v in row_vals)}")

        decisions = {rec["result"].decision for rec in records}
        if len(decisions) > 1:
            print(f"\n  ⚠ scenario 间 decision 跨边界: {decisions}")


def _print_compare_report(deals_results: Dict[str, List[Dict]], snap: Dict) -> None:
    """多 deal 同 panel 的横向对比"""
    print(f"\n{'='*92}")
    print(f"  Multi-deal compare against {snap['snapshot_id']} "
          f"(asof={snap['asof_date']}, n={snap['n_ipos_in_universe']})")
    print(f"{'='*92}")
    aggs = json.loads(snap.get("aggregates_json") or "{}")
    panel_med = (aggs.get("overall") or {}).get("pe_at_offer_p50")

    headers = ["metric"] + list(deals_results.keys()) + ["Panel mid"]
    rows = []

    def _row(label, getter):
        rs = [label]
        for code, recs in deals_results.items():
            mid = next((r for r in recs if r["scenario"] in ("mid", "final")),
                       recs[0])
            rs.append(getter(mid))
        rs.append("-")
        return rs

    rows.append(_row("status",
                     lambda r: str(r["row"]["status"])))
    rows.append(_row("chapter",
                     lambda r: str(r["row"]["listing_chapter"] or "")[:12]))
    rows.append(_row("price",
                     lambda r: f"{r['price']:.2f}"))
    rows.append(_row("NACS_adj",
                     lambda r: f"{r['result'].nacs_adjusted:.4f}"))
    rows.append(_row("decision",
                     lambda r: r["result"].decision))
    rows.append(_row("Q_company",
                     lambda r: f"{r['result'].Q_company:.3f}"))
    rows.append(_row("Q_eco",
                     lambda r: f"{r['result'].Q_ecosystem:.3f}"))
    rows.append(_row("R_lockup",
                     lambda r: f"{r['result'].R_lockup:.3f}"))

    pe_row = ["pe_at_offer"]
    for code, recs in deals_results.items():
        mid = next((r for r in recs if r["scenario"] in ("mid", "final")), recs[0])
        pe = mid["offering"].offering.pe_at_offer
        pe_row.append(f"{pe:.1f}" if pe else "n/a")
    pe_row.append(f"{panel_med:.1f}" if panel_med else "n/a")
    rows.append(pe_row)

    # 打印
    col_w = max(12, max(len(str(c)) for c in headers))
    print(f"\n  {'  '.join(c.ljust(col_w) for c in headers)}")
    print(f"  {'-' * (col_w * len(headers))}")
    for r in rows:
        print(f"  {'  '.join(str(c).ljust(col_w) for c in r)}")


def _print_similar_cases_for_deals(conn, deals_results: Dict[str, List[Dict]]) -> None:
    """从 mv_ipo_full 找每个 deal 的 top-3 similar listed IPO"""
    from data.predictions import find_similar_cases
    print(f"\n  Similar listed IPOs (top-3 by chapter+gics match, last 24 months):")
    cutoff = (date.today().replace(year=date.today().year - 2)).isoformat()
    for code, recs in deals_results.items():
        mid = next((r for r in recs if r["scenario"] in ("mid", "final")), recs[0])
        chapter_val = mid["offering"].listing_chapter.value
        gics = mid["row"]["gics_l2"]
        sims = find_similar_cases(conn, chapter=chapter_val, gics_l2=gics,
                                  q_company=mid["result"].Q_company,
                                  q_ecosystem=mid["result"].Q_ecosystem,
                                  r_lockup=mid["result"].R_lockup,
                                  min_listing_date=cutoff, k=3)
        print(f"\n    [{code}]  chapter={chapter_val}  gics={gics or '--'}")
        if not sims:
            print(f"      (no similar listed IPO found in panel)")
            continue
        for s in sims:
            d30 = f"{s['actual_d30']:+.2%}" if s['actual_d30'] is not None else "n/a"
            m6 = f"{s['actual_m6']:+.2%}" if s['actual_m6'] is not None else "pending"
            dims = "+".join(s['match_dims'])
            print(f"      {s['stock_code']:8s} {(s['name'] or '')[:18]:18s} "
                  f"{s['listing_date']}  match={dims:18s}  d30={d30:>8s}  m6={m6:>8s}")


# =============================================================================
# CLI
# =============================================================================

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--stock-code", help="单 deal 评估")
    g.add_argument("--stock-codes", help="逗号分隔多 deal (--compare 必带)")
    ap.add_argument("--db", default=str(_ROOT / "data" / "nacs_real.db"))
    ap.add_argument("--asof", help="分析切点 YYYY-MM-DD; 默认 deal pricing_date 或 today")
    ap.add_argument("--panel-id", help="指定 panel_snapshot_id; 默认最近一个")
    ap.add_argument("--price-scan", action="store_true",
                    help="区间扫描 (low/mid/high) 各跑一次")
    ap.add_argument("--compare", action="store_true",
                    help="多 deal 横向对比")
    ap.add_argument("--persist", action="store_true",
                    help="把结果写进 nacs_predictions (audit trail)")
    ap.add_argument("--notes", help="本次评估的备注 (写入 prediction.notes)")
    ap.add_argument("--html", metavar="PATH",
                    help="同时输出自包含 HTML IC memo 到 PATH (单文件可邮件分发)")
    ap.add_argument("--ai-revenue-pct", type=float, metavar="PCT",
                    help="AI 业务收入占比 (0-1) override; 优先于 themes/ai_revenue_manual.json")
    ap.add_argument("--no-themes", action="store_true",
                    help="禁用主题情绪 / AI 镀金溢价 panel (只渲染 NACS 决策段)")
    args = ap.parse_args()

    if args.compare and not args.stock_codes:
        print("❌ --compare 需要 --stock-codes", file=sys.stderr)
        return 1

    codes = ([args.stock_code] if args.stock_code else
             [c.strip() for c in args.stock_codes.split(",") if c.strip()])

    with db_connect(args.db) as conn:
        # asof 仅用于 panel 默认值; 实际每个 deal 自己算 asof
        asof_default = date.fromisoformat(args.asof) if args.asof else date.today()
        snap = _ensure_panel_snapshot(conn, asof_default, args.panel_id)

        # S5: 加载 themes/ 数据 (供 thesis.py + renderer 用); --no-themes 时跳过
        themes_bundle = None
        if not args.no_themes:
            from reports.themes_data import load_all
            themes_bundle = load_all(today=asof_default)
            for name, (data, prov) in themes_bundle.items():
                if prov.status == "missing":
                    print(f"  ⚠ themes/{name}: missing", file=sys.stderr)
                elif prov.is_stale:
                    note_str = "; ".join(prov.notes[:1]) if prov.notes else "stale"
                    print(f"  ⚠ themes/{name}: {note_str}", file=sys.stderr)

        # S5: ai_revenue_pct 优先级: CLI > deal YAML themes.ai_revenue_pct >
        #     ai_revenue_manual.json (在 thesis._resolve_ai_revenue_pct 里查)
        ai_pct_per_code: Dict[str, float] = {}
        for code in codes:
            yaml_path = _ROOT / "data" / "deals" / f"{code}.yaml"
            if yaml_path.exists():
                try:
                    import yaml as _yaml
                    deal_data = _yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
                    pct = ((deal_data.get("themes") or {}).get("ai_revenue_pct"))
                    if pct is not None:
                        ai_pct_per_code[code] = float(pct)
                        print(f"  ↳ {code}: ai_revenue_pct={pct} from {yaml_path.name}")
                except (ImportError, OSError, ValueError, TypeError):
                    pass

        deals_results: Dict[str, List[Dict]] = {}
        for code in codes:
            row = conn.execute(
                "SELECT * FROM ipo_master WHERE stock_code = ? "
                "ORDER BY listing_date DESC LIMIT 1", (code,),
            ).fetchone()
            if not row:
                print(f"⚠ {code}: 不在 ipo_master, 跳过 (用 load_deal.py 先灌)",
                      file=sys.stderr)
                continue
            asof = _resolve_asof_for_deal(row, args.asof)
            if args.price_scan:
                scenarios = _scenario_prices(row)
            else:
                # 默认 mid (区间) 或 final (已上市)
                all_scen = _scenario_prices(row)
                scenarios = [next((s for s in all_scen
                                   if s[0] in ("mid", "final")),
                                  all_scen[0])]
            try:
                # P0.2: 把 CLI/YAML 里的 ai_revenue_pct override 传进去
                ai_pct_for_eval = (
                    args.ai_revenue_pct
                    if args.ai_revenue_pct is not None
                    else ai_pct_per_code.get(code)
                )
                results = _evaluate_deal(
                    conn, stock_code=code, asof=asof,
                    scenarios=scenarios, themes_bundle=themes_bundle,
                    ai_revenue_pct_override=ai_pct_for_eval,
                    panel_snap=snap,
                )
            except SystemExit as e:
                print(str(e), file=sys.stderr)
                continue
            deals_results[code] = results

            # 持久化
            if args.persist:
                # 先合成 thesis (含 theme_heat / premium_estimate / themes_provenance)
                # 用 mid/final 主行作为代表
                from reports.thesis import synthesize_thesis
                main_rec = next((r for r in results
                                  if r["scenario"] in ("mid", "final")),
                                results[0])
                ipo_concepts_for_persist = [
                    r[0] for r in conn.execute(
                        "SELECT concept_name FROM ipo_concepts WHERE stock_code = ?",
                        (code,)
                    )
                ]
                # 优先级: CLI > deal YAML > ai_revenue_manual (在 thesis 里查)
                ai_pct_for_code = (
                    args.ai_revenue_pct
                    if args.ai_revenue_pct is not None
                    else ai_pct_per_code.get(code)
                )
                thesis = synthesize_thesis(
                    main_rec["result"],
                    panel_snap=snap, similar_cases=[],
                    themes_bundle=themes_bundle,
                    stock_code=code,
                    gics_l2=row["gics_l2"],
                    ipo_concept_names=ipo_concepts_for_persist,
                    company_name=row["company_name_zh"],
                    ai_revenue_pct_override=ai_pct_for_code,
                )
                for rec in results:
                    case_id = persist_prediction(
                        conn,
                        result=rec["result"], offering=rec["offering"],
                        stock_code=code, asof=asof,
                        panel_snapshot_id=snap["snapshot_id"],
                        deal_status_at_analysis=row["status"],
                        price_scenario=rec["scenario"],
                        offer_price_used=rec["price"],
                        notes=args.notes,
                        thesis=thesis,
                    )
                    print(f"  ✓ persisted: {case_id}"
                          f"{' [theme=' + thesis['theme_heat']['theme_id'] + ']' if thesis.get('theme_heat') else ''}")

        # 输出报告
        if args.compare and len(deals_results) > 1:
            _print_compare_report(deals_results, snap)
            _print_similar_cases_for_deals(conn, deals_results)
        else:
            for code, recs in deals_results.items():
                _print_single_report(recs, snap, _resolve_asof_for_deal(
                    recs[0]["row"], args.asof))
                _print_similar_cases_for_deals(conn, {code: recs})

        # ===== HTML IC memo 输出 =====
        if args.html:
            from data.predictions import find_similar_cases
            from reports.html_renderer import (
                render_single_deal, render_compare, write_html,
            )
            cutoff = (date.today().replace(year=date.today().year - 2)).isoformat()

            if args.compare and len(deals_results) > 1:
                similar_per_deal = {}
                for code, recs in deals_results.items():
                    mid = next((r for r in recs
                                if r["scenario"] in ("mid", "final")), recs[0])
                    chapter_val = mid["offering"].listing_chapter.value
                    gics = mid["row"]["gics_l2"]
                    similar_per_deal[code] = find_similar_cases(
                        conn, chapter=chapter_val, gics_l2=gics,
                        q_company=mid["result"].Q_company,
                        q_ecosystem=mid["result"].Q_ecosystem,
                        r_lockup=mid["result"].R_lockup,
                        min_listing_date=cutoff, k=5,
                    )
                html = render_compare(deals_results, snap, similar_per_deal,
                                      title=args.notes)
            else:
                # 单 deal: 取第一个 (一般也只有一个)
                code, recs = next(iter(deals_results.items()))
                mid = next((r for r in recs
                            if r["scenario"] in ("mid", "final")), recs[0])
                chapter_val = mid["offering"].listing_chapter.value
                gics = mid["row"]["gics_l2"]
                similar = find_similar_cases(
                    conn, chapter=chapter_val, gics_l2=gics,
                    q_company=mid["result"].Q_company,
                    q_ecosystem=mid["result"].Q_ecosystem,
                    r_lockup=mid["result"].R_lockup,
                    min_listing_date=cutoff, k=5,
                )
                # ipo_concepts for classifier (themes panel)
                ipo_concepts_for_render = [
                    r[0] for r in conn.execute(
                        "SELECT concept_name FROM ipo_concepts WHERE stock_code = ?",
                        (code,)
                    )
                ]
                ai_pct_for_render = (
                    args.ai_revenue_pct
                    if args.ai_revenue_pct is not None
                    else ai_pct_per_code.get(code)
                )
                html = render_single_deal(
                    recs, snap,
                    asof=_resolve_asof_for_deal(recs[0]["row"], args.asof),
                    similar_cases=similar,
                    themes_bundle=themes_bundle,
                    ipo_concept_names=ipo_concepts_for_render,
                    ai_revenue_pct_override=ai_pct_for_render,
                )
            out_path = write_html(html, Path(args.html))
            print(f"\n✓ HTML IC memo: {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
