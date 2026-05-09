"""
case_review.py — 上市后复盘单只 deal 的所有历史预测 vs 实际表现

用法:
    python scripts/case_review.py --stock-code 1187.HK
    python scripts/case_review.py --stock-code 1187.HK --json    # 机器可读

复盘内容:
  1. 该 stock 在 nacs_predictions 中的全部历史预测 (按 asof_date 升序)
  2. 当前 ipo_returns 中的实际表现 (受 is_*_due 过滤)
  3. 最后一次预测 (最贴近实际定价那次) 与实际的差异
  4. 当时给出的 similar_cases 实际表现 vs 这只本身的实际, 看模型类比是否准
  5. 跨次预测的稳定性 (NACS std 跨 asof)
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from data.dao import db_connect
from data.predictions import list_predictions_for_stock


def _fmt_pct(v: Optional[float]) -> str:
    return f"{v:+.2%}" if v is not None else "n/a"


def _fmt_num(v: Optional[float], digits: int = 4) -> str:
    return f"{v:.{digits}f}" if v is not None else "n/a"


def _get_ipo_actuals(conn, stock_code: str) -> Optional[Dict[str, Any]]:
    """从 ipo_master + ipo_returns 拿这只 IPO 的实际表现 (受 is_*_due 过滤)"""
    row = conn.execute("""
        SELECT m.ipo_id, m.stock_code, m.company_name_zh, m.status,
               m.listing_date, m.expected_listing_date, m.pricing_date,
               m.listing_chapter, m.gics_l2,
               m.offer_price_hkd, m.offer_price_low, m.offer_price_high,
               m.intl_oversub, m.public_oversub, m.pricing_in_range,
               m.cornerstone_coverage,
               r.return_d1_close, r.return_d30, r.return_m6, r.return_m12,
               r.return_unlock_d30, r.return_unlock_d90,
               r.max_drawdown_m6,
               r.is_d30_due, r.is_m6_due, r.is_m12_due, r.is_unlock_due
        FROM ipo_master m
        LEFT JOIN ipo_returns r ON r.ipo_id = m.ipo_id
        WHERE m.stock_code = ?
        ORDER BY m.listing_date DESC LIMIT 1
    """, (stock_code,)).fetchone()
    if not row:
        return None
    d = dict(row)
    # 应用 due 过滤: 未到期的 actual = None
    for key, due_key in [("return_d30", "is_d30_due"), ("return_m6", "is_m6_due"),
                         ("return_m12", "is_m12_due"),
                         ("return_unlock_d30", "is_unlock_due"),
                         ("return_unlock_d90", "is_unlock_due")]:
        if d.get(due_key) != 1:
            d[key + "_filtered"] = None
        else:
            d[key + "_filtered"] = d.get(key)
    return d


def _diff_inputs_vs_actual(prediction: Dict, ipo_row: Dict) -> List[Dict]:
    """对最后一次 prediction 的 inputs_json 与上市后实际 ipo_master 字段比对.

    展示哪些字段在分析时是估计/区间, 上市后变了多少.
    """
    inputs = json.loads(prediction["inputs_json"])
    offering = inputs.get("offering") or {}

    diffs = []
    pairs = [
        ("intl_oversubscription", "intl_oversub", "国际配售认购"),
        ("public_oversubscription", "public_oversub", "公开认购"),
        ("pricing_in_range", "pricing_in_range", "定价区间位置"),
        ("offering_size_hkd", "offering_size_hkd", "募资额"),
        ("pe_at_offer", "pe_at_offer", "发行 PE"),
    ]
    for input_key, actual_key, label in pairs:
        pred_v = offering.get(input_key)
        actual_v = ipo_row.get(actual_key)
        if pred_v is not None and actual_v is not None:
            try:
                delta = actual_v - pred_v
                pct = (delta / abs(pred_v)) if abs(pred_v) > 1e-9 else None
                diffs.append({
                    "field": label, "pred": pred_v, "actual": actual_v,
                    "delta": delta, "delta_pct": pct,
                })
            except (TypeError, ZeroDivisionError):
                pass
    return diffs


def review(conn, stock_code: str) -> Dict[str, Any]:
    """复盘报告的可机器读形式"""
    preds = list_predictions_for_stock(conn, stock_code)
    actuals = _get_ipo_actuals(conn, stock_code)

    if not actuals:
        return {"stock_code": stock_code, "error": "not in ipo_master"}

    out: Dict[str, Any] = {
        "stock_code": stock_code,
        "company_name_zh": actuals["company_name_zh"],
        "current_status": actuals["status"],
        "listing_date": str(actuals["listing_date"]) if actuals["listing_date"] else None,
        "n_predictions": len(preds),
        "predictions": [],
        "actuals": {
            "return_d30": actuals["return_d30_filtered"],
            "return_m6": actuals["return_m6_filtered"],
            "return_m12": actuals["return_m12_filtered"],
            "max_drawdown_m6": actuals["max_drawdown_m6"],
            "is_d30_due": actuals["is_d30_due"],
            "is_m6_due": actuals["is_m6_due"],
            "is_m12_due": actuals["is_m12_due"],
        },
    }

    if not preds:
        out["error"] = "no predictions yet for this stock"
        return out

    # 跨次稳定性
    nacs_vals = [p["nacs_adjusted"] for p in preds if p["nacs_adjusted"] is not None]
    qc_vals = [p["Q_company"] for p in preds if p["Q_company"] is not None]
    qe_vals = [p["Q_ecosystem"] for p in preds if p["Q_ecosystem"] is not None]
    rl_vals = [p["R_lockup"] for p in preds if p["R_lockup"] is not None]
    out["stability"] = {
        "nacs_std": statistics.stdev(nacs_vals) if len(nacs_vals) > 1 else 0.0,
        "Q_company_std": statistics.stdev(qc_vals) if len(qc_vals) > 1 else 0.0,
        "Q_ecosystem_std": statistics.stdev(qe_vals) if len(qe_vals) > 1 else 0.0,
        "R_lockup_std": statistics.stdev(rl_vals) if len(rl_vals) > 1 else 0.0,
    }

    # 每次预测的简要 (按 asof 排序)
    for p in preds:
        out["predictions"].append({
            "case_id": p["case_id"],
            "asof_date": str(p["asof_date"])[:10],
            "panel_snapshot_id": p["panel_snapshot_id"],
            "deal_status_at_analysis": p["deal_status_at_analysis"],
            "price_scenario": p["price_scenario"],
            "offer_price_used": p["offer_price_used"],
            "nacs_adjusted": p["nacs_adjusted"],
            "decision": p["decision"],
            "Q_company": p["Q_company"],
            "Q_ecosystem": p["Q_ecosystem"],
            "R_lockup": p["R_lockup"],
            "notes": p["notes"],
        })

    # 锁定预测 = 最后一次 (最贴近实际)
    last = preds[-1]
    out["locked_prediction"] = {
        "case_id": last["case_id"],
        "asof_date": str(last["asof_date"])[:10],
        "decision": last["decision"],
        "nacs_adjusted": last["nacs_adjusted"],
    }
    # 该次的 inputs vs actual diff
    out["inputs_vs_actual"] = _diff_inputs_vs_actual(last, actuals)

    # similar_cases 当时给出的 → 实际表现回看
    sim_cases = json.loads(last.get("similar_cases_json") or "[]")
    sim_d30 = [s["actual_d30"] for s in sim_cases if s.get("actual_d30") is not None]
    sim_m6 = [s["actual_m6"] for s in sim_cases if s.get("actual_m6") is not None]
    out["similar_cases"] = {
        "items": sim_cases,
        "d30_median": statistics.median(sim_d30) if sim_d30 else None,
        "m6_median": statistics.median(sim_m6) if sim_m6 else None,
    }
    out["similar_d30_diff"] = None
    out["similar_m6_diff"] = None
    if actuals["return_d30_filtered"] is not None and out["similar_cases"]["d30_median"] is not None:
        out["similar_d30_diff"] = (actuals["return_d30_filtered"]
                                   - out["similar_cases"]["d30_median"])
    if actuals["return_m6_filtered"] is not None and out["similar_cases"]["m6_median"] is not None:
        out["similar_m6_diff"] = (actuals["return_m6_filtered"]
                                  - out["similar_cases"]["m6_median"])

    return out


def print_review(rep: Dict[str, Any]) -> None:
    print(f"\n{'=' * 72}")
    print(f"  Case Review: {rep['stock_code']} {rep.get('company_name_zh') or ''}")
    print(f"  Current status: {rep.get('current_status')}  "
          f"Listing date: {rep.get('listing_date')}")
    print(f"{'=' * 72}")

    if rep.get("error"):
        print(f"\n  ⚠ {rep['error']}")
        return

    # 1. 预测历史
    print(f"\n  Prediction history ({rep['n_predictions']} runs):")
    for p in rep["predictions"]:
        print(f"    asof {p['asof_date']}  status={p['deal_status_at_analysis']:11s} "
              f"scen={p['price_scenario']:5s}  NACS={_fmt_num(p['nacs_adjusted'])}  "
              f"{p['decision']:14s}  panel={p['panel_snapshot_id']}")

    # 2. 跨次稳定性
    s = rep["stability"]
    print(f"\n  Stability across runs (std):")
    print(f"    NACS_adj      : {_fmt_num(s['nacs_std'])}")
    print(f"    Q_company     : {_fmt_num(s['Q_company_std'])}")
    print(f"    Q_ecosystem   : {_fmt_num(s['Q_ecosystem_std'])}")
    print(f"    R_lockup      : {_fmt_num(s['R_lockup_std'])}")

    # 3. 锁定预测 vs 实际
    locked = rep["locked_prediction"]
    a = rep["actuals"]
    print(f"\n  Locked prediction (last run, asof {locked['asof_date']}):")
    print(f"    decision = {locked['decision']}, NACS = {_fmt_num(locked['nacs_adjusted'])}")
    print(f"\n  Actuals (asof now):")
    if a["is_d30_due"] != 1:
        print(f"    d30: not yet due")
    else:
        print(f"    d30: {_fmt_pct(a['return_d30'])}")
    if a["is_m6_due"] != 1:
        print(f"    m6 : not yet due")
    else:
        print(f"    m6 : {_fmt_pct(a['return_m6'])}")
    if a["is_m12_due"] != 1:
        print(f"    m12: not yet due")
    else:
        print(f"    m12: {_fmt_pct(a['return_m12'])}")
    if a["max_drawdown_m6"] is not None:
        print(f"    max_dd_m6: {_fmt_pct(a['max_drawdown_m6'])}")

    # 4. inputs vs actual diff
    diffs = rep.get("inputs_vs_actual") or []
    if diffs:
        print(f"\n  Inputs at analysis-time vs final actual:")
        print(f"    {'field':<20s}  {'pred':>12s}  {'actual':>12s}  {'delta':>10s}  {'delta_pct':>10s}")
        for d in diffs:
            print(f"    {d['field']:<20s}  {d['pred']:>12.2f}  {d['actual']:>12.2f}  "
                  f"{d['delta']:>+10.2f}  "
                  f"{(d['delta_pct']*100):+9.1f}%" if d['delta_pct'] is not None
                  else f"    {d['field']:<20s}  ...")

    # 5. similar_cases 反查
    sim = rep.get("similar_cases", {})
    if sim.get("items"):
        print(f"\n  Similar listed IPOs (from last prediction's similar_cases) "
              f"vs this stock:")
        for s in sim["items"]:
            print(f"    {s['stock_code']:8s} {(s['name'] or '')[:18]:18s}  "
                  f"d30={_fmt_pct(s.get('actual_d30')):>8s}  "
                  f"m6={_fmt_pct(s.get('actual_m6')):>8s}")
        if sim.get("d30_median") is not None:
            print(f"\n    similar d30 median: {_fmt_pct(sim['d30_median'])}")
            this_d30 = a.get("return_d30")
            if this_d30 is not None and a.get("is_d30_due") == 1:
                diff = this_d30 - sim["d30_median"]
                print(f"    THIS    d30        : {_fmt_pct(this_d30)}  "
                      f"(vs similar median: {diff:+.2%})")
        if sim.get("m6_median") is not None:
            print(f"    similar m6  median: {_fmt_pct(sim['m6_median'])}")
            this_m6 = a.get("return_m6")
            if this_m6 is not None and a.get("is_m6_due") == 1:
                diff = this_m6 - sim["m6_median"]
                print(f"    THIS    m6         : {_fmt_pct(this_m6)}  "
                      f"(vs similar median: {diff:+.2%})")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stock-code", required=True)
    ap.add_argument("--db", default=str(_ROOT / "data" / "nacs_real.db"))
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    ap.add_argument("--html", metavar="PATH",
                    help="同时输出自包含 HTML 复盘报告到 PATH")
    args = ap.parse_args()

    with db_connect(args.db) as conn:
        rep = review(conn, args.stock_code)

    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
    else:
        print_review(rep)

    if args.html:
        from reports.html_renderer import render_case_review, write_html
        html = render_case_review(rep)
        out_path = write_html(html, Path(args.html))
        print(f"\n✓ HTML case review: {out_path}")

    return 0 if not rep.get("error") else 1


if __name__ == "__main__":
    raise SystemExit(main())
