"""
NACS v7 完整回测 - 一键执行

用法:
    python run_v7_backtest.py
    python run_v7_backtest.py --db data/nacs_real.db --out outputs/

功能:
    1. 加载 SQLite 数据库
    2. 对每只 IPO 计算 v7 评分 (含 regime gate + cluster bonus)
    3. 输出评分 CSV + IC 报告
"""
import argparse
import sys
import math
import json
import sqlite3
from datetime import date
from pathlib import Path
from collections import Counter

# Windows 控制台 UTF-8 (避免 emoji 触发 GBK 编码错误)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 让 src/ 进入 import path
ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(ROOT / 'src'))

import pandas as pd
import numpy as np

from nacs_model import (
    IPOOffering, ListingChapter, CompanyType, SponsorTier, compute_nacs,
    OfferingStructure, SponsorInfo, MarketEnvironment, LockupContext,
    ProfitableFundamentals, BiotechFundamentals, TechC18Fundamentals,
    CornerstoneInvestor, CornerstoneType, compute_regime_score,
)
from data.dao import compute_cornerstone_perf_asof, fetch_market_env_at, _FALLBACK_MARKET_ENV


def db_connect(path):
    """SQLite 连接 (返回 dict-like row)"""
    conn = sqlite3.connect(path, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    return conn


def hydrate_cornerstones(conn, ipo_id, asof):
    """加载 IPO 的所有基石 + 计算 cluster_count (同 ultimate_holder ≥2)"""
    ipo = conn.execute("SELECT * FROM ipo_master WHERE ipo_id=?", (ipo_id,)).fetchone()
    rows = conn.execute("""
        SELECT cornerstone_id, ticket_size_hkd, affiliation_flag, affiliation_reason
        FROM ipo_cornerstone_link WHERE ipo_id=?
    """, (ipo_id,)).fetchall()

    investors = []
    cluster_count = 0
    for r in rows:
        cs_id = r["cornerstone_id"]
        master = conn.execute(
            "SELECT * FROM cornerstone_master WHERE cornerstone_id=?", (cs_id,)
        ).fetchone()
        if not master:
            continue

        # 性能快照 (优先用 asof 缓存, 否则实时计算)
        snap = conn.execute("""
            SELECT * FROM cornerstone_performance_asof
            WHERE cornerstone_id=? AND as_of_date<=?
            ORDER BY as_of_date DESC LIMIT 1
        """, (cs_id, asof.isoformat())).fetchone()
        if snap:
            sd = snap["as_of_date"]
            sd = sd if isinstance(sd, date) else date.fromisoformat(str(sd))
            if (asof - sd).days <= 90:
                perf = {
                    "ipo_count_5y": snap["ipo_count_5y"],
                    "avg_m6_return_5y": snap["avg_m6_return_5y"],
                    "winrate_m6_5y": snap["winrate_m6_5y"],
                    "lockup_discipline_score": snap["lockup_discipline_score"],
                    "sector_expertise_dict": json.loads(snap["sector_expertise"] or "{}"),
                }
            else:
                perf = compute_cornerstone_perf_asof(conn, cs_id, asof)
        else:
            perf = compute_cornerstone_perf_asof(conn, cs_id, asof)

        sect = perf["sector_expertise_dict"].get(ipo["gics_l2"], 0) if ipo["gics_l2"] else 0
        flag = r["affiliation_flag"] or 0
        # flag=1 强关联 (公司名匹配) → True; flag=2 簇基石 → cluster_count, 不算 affil
        is_affil = (flag == 1)
        if flag == 2:
            cluster_count += 1

        investors.append(CornerstoneInvestor(
            name=master["canonical_name"],
            ticket_size_hkd=r["ticket_size_hkd"] or 0.0,
            type=CornerstoneType(master["cornerstone_type"]),
            aum_usd=master["aum_usd_latest"],
            hk_ipo_count_5y=perf["ipo_count_5y"],
            hk_ipo_avg_m6_return=perf["avg_m6_return_5y"],
            hk_ipo_winrate_m6=perf["winrate_m6_5y"],
            lockup_discipline_score=perf["lockup_discipline_score"],
            sector_expertise=sect,
            affiliation_flag=is_affil,
            affiliation_reason=r["affiliation_reason"] if is_affil else None,
        ))
    return investors, cluster_count


def get_financials(conn, sc):
    """加载 IPO 公司的历年财务"""
    rows = conn.execute("""
        SELECT report_year, revenue_cny, gross_margin, net_margin, roe
        FROM ipo_financials WHERE stock_code=? ORDER BY report_year
    """, (sc,)).fetchall()
    if not rows:
        return None
    by = {}
    for r in rows:
        if any(x is not None for x in [r[1], r[2], r[3], r[4]]):
            by[r[0]] = {
                'revenue': r[1], 'gm': r[2], 'nm': r[3], 'roe': r[4]
            }
    return by if by else None


def derive_profitable(fin):
    """从财务数据派生 ProfitableFundamentals (含 v6.5 fallback 修正)"""
    if not fin:
        return ProfitableFundamentals(
            revenue_cagr_3y=0.05, gross_margin_trend=-0.01,
            roe_avg_3y=0.06, net_debt_to_ebitda=2.5, fcf_positive_years=1
        )
    yrs = sorted(fin.keys())
    rev = [fin[y]['revenue'] for y in yrs if fin[y].get('revenue')]
    if len(rev) >= 2 and rev[0] > 0:
        rev_cagr = max(-0.5, min(2.0, (rev[-1]/rev[0])**(1/(len(rev)-1)) - 1))
    else:
        rev_cagr = 0.05
    gms = [fin[y]['gm'] for y in yrs if fin[y].get('gm') is not None]
    gm_trend = (gms[-1] - gms[0]) / 100 if len(gms) >= 2 else -0.005
    roes = [fin[y]['roe'] for y in yrs if fin[y].get('roe') is not None]
    roe_avg = (sum(roes) / len(roes) / 100) if roes else 0.06
    nms = [fin[y]['nm'] for y in yrs if fin[y].get('nm') is not None]
    avg_nm = sum(nms) / len(nms) if nms else 3.0
    nd = 0.5 if avg_nm >= 15 else (1.5 if avg_nm >= 5 else (3.0 if avg_nm >= 0 else 5.0))
    fcf = min(3, sum(1 for y in yrs if (fin[y].get('nm') or -1) > 0)) if nms else 1
    return ProfitableFundamentals(
        revenue_cagr_3y=rev_cagr, gross_margin_trend=gm_trend,
        roe_avg_3y=roe_avg, net_debt_to_ebitda=nd, fcf_positive_years=fcf
    )


def build_offering(conn, ipo_id, regime_score, *, use_static_env: bool = False):
    """从 DB 数据构造 IPOOffering (含 v7 字段)

    Args:
        use_static_env: True -> 用旧硬编码 fallback (基线 A);
                        False -> 调 fetch_market_env_at 走 iFinD/JSON/cache (实时 B)
    """
    row = conn.execute("SELECT * FROM ipo_master WHERE ipo_id=?", (ipo_id,)).fetchone()
    if not row:
        return None
    sc = row["stock_code"]
    pd_val = row["pricing_date"]
    asof = pd_val if isinstance(pd_val, date) else date.fromisoformat(str(pd_val)[:10])
    cs, cluster_count = hydrate_cornerstones(conn, ipo_id, asof)

    chapter = ListingChapter(row["listing_chapter"])
    if chapter == ListingChapter.CHAPTER_18A:
        ctype = CompanyType.BIOTECH_18A
    elif chapter in (ListingChapter.CHAPTER_18C_COMMERCIAL, ListingChapter.CHAPTER_18C_PRECOMMERCIAL):
        ctype = CompanyType.TECH_18C
    else:
        ctype = CompanyType.PROFITABLE

    if use_static_env:
        market = MarketEnvironment(**_FALLBACK_MARKET_ENV)
    else:
        market = fetch_market_env_at(conn, asof, allow_ifind=True)
    lockup = LockupContext(
        lockup_months=row["lockup_months"] or 6, overhang_ratio=1.0,
        fundamental_risk_score=0.30, peer_lockup_avg_drawdown=0.10, pe_vs_history_pct=0.50,
    )
    profitable = derive_profitable(get_financials(conn, sc)) if ctype == CompanyType.PROFITABLE else None
    biotech = BiotechFundamentals(
        core_pipeline_phase="II", pipeline_count_phase2plus=2,
        cash_runway_months=18, bd_deals_count_2y=1
    ) if ctype == CompanyType.BIOTECH_18A else None
    tech18c = TechC18Fundamentals(
        is_commercial=(chapter == ListingChapter.CHAPTER_18C_COMMERCIAL),
        revenue_growth_yoy=0.30, milestone_score=3.0, rd_intensity=0.20
    ) if ctype == CompanyType.TECH_18C else None

    return IPOOffering(
        company_name=row["company_name_zh"] or sc, stock_code=sc,
        listing_chapter=chapter, company_type=ctype,
        is_a_h=bool(row["is_a_h"]),
        a_share_short_borrowable=bool(row["is_a_h"]),
        cornerstones=cs,
        offering=OfferingStructure(
            pricing_in_range=row["pricing_in_range"] or 0.7,
            intl_oversubscription=row["intl_oversub"] or 3.0,
            public_oversubscription=row["public_oversub"] or 5.0,
            clawback_triggered=bool(row["clawback_triggered"]),
            greenshoe_pct=row["greenshoe_pct"] or 0.15,
            offering_size_hkd=row["offering_size_hkd"] or 1e9,
            pe_at_offer=row["pe_at_offer"], pe_peer_median=row["pe_peer_median"],
            last_round_premium=row["last_round_premium"], auditor_tier=1,
        ),
        sponsor=SponsorInfo(
            primary_sponsor=row["sponsor_primary"] or "Unknown",
            primary_tier=SponsorTier(row["sponsor_tier"] or 2), joint_sponsor_count=1,
        ),
        market=market, lockup=lockup,
        profitable=profitable, biotech=biotech, tech18c=tech18c,
        regime_score=regime_score,
        cluster_cornerstone_count=cluster_count,
    )


# ===== IC 工具 =====
def ic(scores, rets):
    pairs = [(s, r) for s, r in zip(scores, rets) if pd.notna(s) and pd.notna(r)]
    if len(pairs) < 5:
        return float('nan'), 0
    s, r = zip(*pairs)
    return float(pd.Series(s).rank().corr(pd.Series(r).rank())), len(pairs)


def long_short(scores, rets, q=0.2):
    pairs = [(float(s), float(r)) for s, r in zip(scores, rets)
             if pd.notna(s) and pd.notna(r)]
    if len(pairs) < 10:
        return None
    pairs.sort(key=lambda x: x[0])
    n = len(pairs)
    k = max(2, int(n * q))
    bot = [r for _, r in pairs[:k]]
    top = [r for _, r in pairs[-k:]]
    if len(top) < 2 or len(bot) < 2:
        return None
    sp = float(np.mean(top)) - float(np.mean(bot))
    se = math.sqrt(np.std(top, ddof=1)**2 / len(top) + np.std(bot, ddof=1)**2 / len(bot))
    return {'spread': sp, 't_stat': sp / se if se > 0 else 0}


# ===== 主流程 =====
def main():
    parser = argparse.ArgumentParser(description='NACS v7 完整回测')
    parser.add_argument('--db', default=str(ROOT / 'data' / 'nacs_real.db'),
                        help='SQLite DB 路径 (默认: data/nacs_real.db)')
    parser.add_argument('--out', default=str(ROOT / 'outputs'),
                        help='输出目录 (默认: outputs/)')
    parser.add_argument('--use-static-env', action='store_true',
                        help='使用旧的硬编码 MarketEnvironment 默认值 (baseline 对照组)')
    args = parser.parse_args()

    db_path = Path(args.db)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not db_path.exists():
        print(f"❌ DB 不存在: {db_path}")
        sys.exit(1)

    print(f"DB: {db_path}")
    print(f"输出: {out_dir}")
    print(f"="*60)

    records = []
    errors = Counter()

    conn = db_connect(db_path)

    # 1. 加载所有 IPO 历史 (供 regime detector)
    all_ipos = conn.execute("""
        SELECT m.ipo_id, m.listing_date, r.return_d30
        FROM ipo_master m LEFT JOIN ipo_returns r ON m.ipo_id = r.ipo_id
        ORDER BY m.listing_date
    """).fetchall()
    history = [
        (date.fromisoformat(str(x["listing_date"])[:10]) if x["listing_date"] else None,
         x["return_d30"])
        for x in all_ipos
    ]

    # 2. 评分每只 IPO
    rows = conn.execute("""
        SELECT m.ipo_id, m.stock_code, m.company_name_zh, m.listing_date,
               m.listing_chapter, m.pricing_date,
               r.return_d1_close, r.return_d30, r.return_m3, r.return_m6
        FROM ipo_master m LEFT JOIN ipo_returns r ON m.ipo_id = r.ipo_id
        WHERE (m.is_delisted=0 OR m.is_delisted IS NULL)
        ORDER BY m.listing_date
    """).fetchall()

    print(f"加载 {len(rows)} 只 IPO 候选, 开始评分...")

    for row in rows:
        try:
            pd_val = row["pricing_date"]
            asof = pd_val if isinstance(pd_val, date) else date.fromisoformat(str(pd_val)[:10])
            regime = compute_regime_score(history, asof)

            offering = build_offering(conn, row["ipo_id"], regime,
                                      use_static_env=args.use_static_env)
            if not offering:
                continue

            r = compute_nacs(offering)
            records.append({
                'ipo_id': row["ipo_id"], 'stock_code': row["stock_code"],
                'name': row["company_name_zh"],
                'listing_date': str(row["listing_date"])[:10] if row["listing_date"] else None,
                'listing_chapter': row["listing_chapter"],
                'NACS': r.nacs_adjusted, 'Q_company': r.Q_company,
                'Q_ecosystem': r.Q_ecosystem, 'R_lockup': r.R_lockup,
                'decision': r.decision, 'position_pct': r.position_pct,
                'regime_score': regime,
                'cluster_count': offering.cluster_cornerstone_count,
                'r5d': row["return_d1_close"], 'r30d': row["return_d30"],
                'r60d': row["return_m3"], 'r180d': row["return_m6"],
            })
        except Exception as e:
            errors[type(e).__name__] += 1

    conn.close()
    print(f"评分: {len(records)} 只, 失败: {dict(errors) if errors else 0}\n")

    df = pd.DataFrame(records)
    df['listing_date'] = pd.to_datetime(df['listing_date'], errors='coerce')
    df['year'] = df['listing_date'].dt.year

    # 3. 决策分布
    dec = Counter(r['decision'] for r in records)
    print("决策分布:")
    for d in ["FULL", "LARGE", "TRIAL", "RELATIONSHIP", "SKIP"]:
        n = dec.get(d, 0)
        print(f"  {d:<14}: {n:>4} ({n/len(records)*100:>5.1f}%)")

    print(f"\nregime_gate 强制 SKIP: "
          f"{((df['regime_score']<0) & (df['decision']=='SKIP')).sum()} 只")
    print(f"cluster bonus 应用 (cluster≥2): {(df['cluster_count']>=2).sum()} 只")

    # 4. 主板 IC
    mb = df[df['listing_chapter'] == 'main_board_profitable']
    print(f"\n主板已盈利 v7 (n={len(mb)}):")
    for col, lbl in [('r5d', '5日'), ('r30d', '30日'),
                     ('r60d', '60日'), ('r180d', '180日')]:
        ic_v, n = ic(mb['NACS'], mb[col])
        ls_v = long_short(mb['NACS'].values, mb[col].values)
        sp = ls_v['spread'] if ls_v else 0
        t = ls_v['t_stat'] if ls_v else 0
        crit = 1.96 / math.sqrt(n) if n > 0 else 999
        sig_ic = '✓' if abs(ic_v) > crit else ' '
        sig_t = '✅' if abs(t) > 2 else ('🟡' if abs(t) > 1.5 else '❌')
        print(f"  {lbl:<5} ic={ic_v:>+.4f}{sig_ic}  L-S={sp:>+.2%} t={t:>+.2f}{sig_t}")

    # 5. 主板 60d 按年份
    print("\n主板 60d IC 按年份:")
    for yr in sorted(mb['year'].dropna().unique()):
        yrdf = mb[mb['year'] == yr]
        if len(yrdf) < 8:
            continue
        ic_v, n = ic(yrdf['NACS'], yrdf['r60d'])
        ls_v = long_short(yrdf['NACS'].values, yrdf['r60d'].values)
        sp = ls_v['spread'] if ls_v else 0
        print(f"  {int(yr):<6} n={n:>3}  ic={ic_v:>+.4f}  L-S={sp:>+.2%}")

    # 6. 保存
    out_csv = out_dir / 'nacs_v7_scores.csv'
    df.to_csv(out_csv, index=False)
    print(f"\n✓ 评分输出: {out_csv}")

    # Regime gate 过滤后的子样本
    df_passed = mb[(mb['regime_score'].notna()) & (mb['regime_score'] >= 0)]
    if len(df_passed) > 20:
        ic_p, n_p = ic(df_passed['NACS'], df_passed['r60d'])
        ls_p = long_short(df_passed['NACS'].values, df_passed['r60d'].values)
        print(f"\n[Regime Gate ≥0 过滤后子样本] 主板 60d:")
        print(f"  n={n_p}  ic={ic_p:+.4f}  L-S={ls_p['spread']:+.2%} t={ls_p['t_stat']:+.2f}")

    # ★ v8 production 实战回报 (砍 RELATIONSHIP 后真正下钱的仓位)
    print(f"\n[Production 实战回报] 主板 regime≥0 + 实际下钱 (LARGE/TRIAL/FULL):")
    prod = mb[(mb['regime_score'].notna()) & (mb['regime_score'] >= 0)
              & (mb['decision'].isin(['FULL', 'LARGE', 'TRIAL']))]
    for col, lbl in [('r5d', '5日'), ('r30d', '30日'), ('r60d', '60日'), ('r180d', '180日')]:
        s = prod[col].dropna()
        if len(s) < 3:
            continue
        win = (s > 0).sum() / len(s)
        print(f"  {lbl:<5} n={len(s):>3}  mean={s.mean():>+.2%}  median={s.median():>+.2%}  win={win:>5.1%}")

    # v8 改动验证: 不应有任何 IPO 被分到 RELATIONSHIP 实际仓位 (position_pct=0)
    rel_decisions = mb[mb['decision'] == 'RELATIONSHIP']
    print(f"\n[v8 RELATIONSHIP 决策标签] n={len(rel_decisions)} (仓位=0, 仅诊断用途)")

    # ===== 输出 IC 摘要 JSON (供 ablation 脚本读取) =====
    def _ic_dict(df_sub):
        result = {}
        for col, key in [('r5d', '5d'), ('r30d', '30d'), ('r60d', '60d'), ('r180d', '180d')]:
            iv, n = ic(df_sub['NACS'], df_sub[col])
            ls = long_short(df_sub['NACS'].values, df_sub[col].values)
            result[key] = {
                'ic': None if pd.isna(iv) else float(iv),
                'n': int(n),
                'ls_spread': float(ls['spread']) if ls else None,
                'ls_t_stat': float(ls['t_stat']) if ls else None,
            }
        return result

    summary = {
        'mode': 'static' if args.use_static_env else 'realtime',
        'n_total': len(records),
        'main_board': _ic_dict(mb),
    }
    if len(df_passed) > 20:
        summary['regime_pass'] = _ic_dict(df_passed)

    suffix = 'static' if args.use_static_env else 'realtime'
    ic_json_path = out_dir / f'backtest_ic_{suffix}.json'
    ic_json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                            encoding='utf-8')
    print(f"\n✓ IC 摘要: {ic_json_path}")


if __name__ == "__main__":
    main()
