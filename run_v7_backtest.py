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


def get_financials(conn, sc, asof=None):
    """加载 IPO 公司的历年财务

    P0-#1 修复: 严格防 look-ahead. 仅返回 report_year < asof.year 的财务,
    因为 IPO 在 pricing_date (= asof) 时, 上一日历年财报通常已审计披露,
    而当年及之后的年报尚不可知. 旧版本无过滤会让 2022 上市 IPO 读到
    2023-2025 的数据, 把 IC 虚高.

    Args:
        sc: stock_code
        asof: pricing_date (date or ISO str). None -> 兼容旧调用 (不过滤),
              仅供 legacy 测试使用; 生产路径必须传值.
    """
    cutoff_year = None
    if asof is not None:
        if isinstance(asof, str):
            cutoff_year = int(asof[:4])
        else:
            cutoff_year = asof.year

    if cutoff_year is not None:
        rows = conn.execute("""
            SELECT report_year, revenue_cny, gross_margin, net_margin, roe
            FROM ipo_financials WHERE stock_code=? AND report_year < ?
            ORDER BY report_year
        """, (sc, cutoff_year)).fetchall()
    else:
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
    # asof 优先级: pricing_date > expected_listing_date - 7 天 > listing_date - 7 天 > today
    # (prospectus deals 没 pricing_date, 用 expected 减一周近似定价日)
    pd_val = row["pricing_date"]
    if pd_val is not None and str(pd_val) not in ("None", "--", ""):
        asof = pd_val if isinstance(pd_val, date) else date.fromisoformat(str(pd_val)[:10])
    else:
        from datetime import timedelta as _td
        for fallback_col in ("expected_listing_date", "listing_date"):
            fallback = row[fallback_col] if fallback_col in row.keys() else None
            if fallback and str(fallback) not in ("None", "--", ""):
                fb = fallback if isinstance(fallback, date) else \
                     date.fromisoformat(str(fallback)[:10])
                asof = fb - _td(days=7)
                break
        else:
            asof = date.today()
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
    # P1-#8 修复: LockupContext 全部 4 字段从 ipo_master 读 (csv 间接计算).
    #   fundamental_risk_score / pe_vs_history_pct: scripts/fix_p1_lockup_context.py
    #   overhang_ratio (= pre/post_shares) / peer_lockup_avg_drawdown:
    #       scripts/fix_p1_lockup_context_v2.py (来自 ifind_share_capital.csv 与 ipo_returns.return_d30)
    _keys = row.keys()
    _fund_risk = row["fundamental_risk_score"] if "fundamental_risk_score" in _keys else None
    _pe_pct = row["pe_vs_history_pct"] if "pe_vs_history_pct" in _keys else None
    _overhang = row["overhang_ratio"] if "overhang_ratio" in _keys else None
    _peer_dd = row["peer_lockup_avg_drawdown"] if "peer_lockup_avg_drawdown" in _keys else None
    lockup = LockupContext(
        lockup_months=row["lockup_months"] or 6,
        overhang_ratio=_overhang if _overhang is not None else 1.0,
        fundamental_risk_score=_fund_risk if _fund_risk is not None else 0.30,
        peer_lockup_avg_drawdown=_peer_dd if _peer_dd is not None else 0.10,
        pe_vs_history_pct=_pe_pct if _pe_pct is not None else 0.50,
    )
    profitable = derive_profitable(get_financials(conn, sc, asof=asof)) if ctype == CompanyType.PROFITABLE else None
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
            # P1.1: mkt_cap = post_ipo_shares × offer_price (None 时 modifier 跳过)
            mkt_cap_at_offer_hkd=(
                (row["post_ipo_shares"] * row["offer_price_hkd"])
                if (row["post_ipo_shares"] and row["offer_price_hkd"])
                else None
            ),
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


# =============================================================================
# 并行化: ProcessPoolExecutor worker (P2-C)
# =============================================================================
# 关键约束:
#   - sqlite3 connection 不可跨进程: 每个 worker 必须自开 conn
#   - worker 必须是 module-level 函数 (可 pickle)
#   - history 是 list[(date, float)], 可 pickle 跨进程
#   - NacsConfig 是单例: worker 进程必须重新 set_config(load_config(path))


def score_one_ipo(args):
    """单 IPO 评分 worker (可 pickle, 跨进程调用).

    Args (单 tuple, 便于 executor.map):
        db_path: str           SQLite 路径
        ipo_id: str
        history: list          [(date, return_d30), ...] 全 IPO 历史 (regime 用)
        use_static_env: bool
        config_path: str|None  NacsConfig YAML/JSON; None=用默认 v8

    Returns:
        dict | None | {"_error": "..."}: 评分记录 / 跳过 / 异常
    """
    db_path, ipo_id, history, use_static_env, config_path = args
    try:
        # worker 进程: 重新加载配置 (单例不会跨进程传递)
        if config_path:
            from config import load_config, set_config
            set_config(load_config(config_path))

        conn = db_connect(db_path)
        try:
            row = conn.execute(
                "SELECT ipo_id, stock_code, company_name_zh, listing_date, "
                "listing_chapter, pricing_date FROM ipo_master WHERE ipo_id=?",
                (ipo_id,),
            ).fetchone()
            if not row:
                return None
            ret = conn.execute(
                "SELECT return_d1_close, return_d30, return_m3, return_m6 "
                "FROM ipo_returns WHERE ipo_id=?", (ipo_id,)
            ).fetchone()

            pd_val = row["pricing_date"]
            asof = pd_val if isinstance(pd_val, date) else \
                date.fromisoformat(str(pd_val)[:10])
            regime = compute_regime_score(history, asof)

            offering = build_offering(conn, ipo_id, regime,
                                      use_static_env=use_static_env)
            if not offering:
                return None

            r = compute_nacs(offering)
            return {
                'ipo_id': row["ipo_id"], 'stock_code': row["stock_code"],
                'name': row["company_name_zh"],
                'listing_date': str(row["listing_date"])[:10] if row["listing_date"] else None,
                'listing_chapter': row["listing_chapter"],
                'NACS': r.nacs_adjusted, 'Q_company': r.Q_company,
                'Q_ecosystem': r.Q_ecosystem, 'R_lockup': r.R_lockup,
                'decision': r.decision, 'position_pct': r.position_pct,
                'regime_score': regime,
                'cluster_count': offering.cluster_cornerstone_count,
                'r5d': ret["return_d1_close"] if ret else None,
                'r30d': ret["return_d30"] if ret else None,
                'r60d': ret["return_m3"] if ret else None,
                'r180d': ret["return_m6"] if ret else None,
            }
        finally:
            conn.close()
    except Exception as e:
        return {"_error": f"{type(e).__name__}: {e}", "_ipo_id": ipo_id}


def parallel_score_ipos(db_path, ipo_ids, history, *,
                        workers: int = 1,
                        use_static_env: bool = False,
                        config_path: str | None = None):
    """并行评分 IPO 列表.

    workers <= 1: 串行 (避免 spawn 开销, 也保证测试可调试)
    workers >= 2: ProcessPoolExecutor

    返回: (records, errors_counter)
    """
    args_list = [(str(db_path), iid, history, use_static_env, config_path)
                 for iid in ipo_ids]
    records = []
    errors = Counter()

    if workers <= 1:
        results = (score_one_ipo(a) for a in args_list)
    else:
        from concurrent.futures import ProcessPoolExecutor
        ex = ProcessPoolExecutor(max_workers=workers)
        try:
            # chunksize: 让每个 worker 一次拿 ~50 个 IPO 而非 1 个 (减少 IPC)
            chunksize = max(1, len(args_list) // (workers * 4))
            results = list(ex.map(score_one_ipo, args_list, chunksize=chunksize))
        finally:
            ex.shutdown(wait=True)

    for r in results:
        if r is None:
            continue
        if isinstance(r, dict) and "_error" in r:
            errors[r["_error"].split(":")[0]] += 1
            continue
        records.append(r)
    return records, errors


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
    parser.add_argument('--config', default=None,
                        help='NacsConfig YAML/JSON 路径 (默认: 内置 v8 硬编码值, '
                             '与 configs/nacs_v8.yaml 等价)')
    parser.add_argument('--workers', type=int, default=1,
                        help='并行 worker 数 (默认 1=串行; >1 走 ProcessPoolExecutor)')
    parser.add_argument('--skip-panel-snapshot', action='store_true',
                        help='不写 panel_snapshots 行 (实验/调试用; 生产应保留写入)')
    args = parser.parse_args()

    # 加载参数化配置 (可选; 不传则用 src/config.py 中默认 = v8 原硬编码)
    if args.config:
        from config import load_config, set_config
        cfg = load_config(args.config)
        set_config(cfg)
        print(f"✓ 已加载配置: {args.config} (version={cfg.version})")

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

    # 1. 加载所有 listed IPO 历史 (供 regime detector + panel 边界)
    #    严格只用 status='listed' (排除 prospectus/pricing 中的 deal pipeline 数据)
    all_ipos = conn.execute("""
        SELECT m.ipo_id, m.listing_date, r.return_d30
        FROM ipo_master m LEFT JOIN ipo_returns r ON m.ipo_id = r.ipo_id
        WHERE m.status = 'listed'
        ORDER BY m.listing_date
    """).fetchall()
    history = [
        (date.fromisoformat(str(x["listing_date"])[:10]) if x["listing_date"] else None,
         x["return_d30"])
        for x in all_ipos
    ]

    # 2. 评分每只 IPO (panel 成员严格 status='listed')
    rows = conn.execute("""
        SELECT m.ipo_id, m.stock_code, m.company_name_zh, m.listing_date,
               m.listing_chapter, m.pricing_date,
               r.return_d1_close, r.return_d30, r.return_m3, r.return_m6
        FROM ipo_master m LEFT JOIN ipo_returns r ON m.ipo_id = r.ipo_id
        WHERE m.status = 'listed'
        ORDER BY m.listing_date
    """).fetchall()

    print(f"加载 {len(rows)} 只 IPO 候选, 开始评分 (workers={args.workers})...")
    conn.close()  # 主进程关闭, 各 worker 自开 conn

    ipo_ids = [r["ipo_id"] for r in rows]
    records, errors = parallel_score_ipos(
        db_path=db_path,
        ipo_ids=ipo_ids,
        history=history,
        workers=args.workers,
        use_static_env=args.use_static_env,
        config_path=args.config,
    )
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

    # ===== 写 panel_snapshots =====
    # 单 deal 评估 (analyze_deal.py) 后续会引用最近的 snapshot_id 锁上下文.
    if not args.skip_panel_snapshot:
        from data.panel_snapshot import write_panel_snapshot
        from data.dao import fetch_market_env_at as _fmenv
        from config import get_config as _get_cfg
        with db_connect(db_path) as snap_conn:
            asof_today = date.today()
            try:
                market_env = _fmenv(snap_conn, asof_today, allow_ifind=False)
            except Exception:
                market_env = None
            cfg = _get_cfg()
            cfg_dict = cfg.to_dict() if hasattr(cfg, 'to_dict') else {}
            cfg_yaml = None
            if args.config:
                try:
                    cfg_yaml = Path(args.config).read_text(encoding='utf-8')
                except OSError:
                    cfg_yaml = None
            avg_regime = (df_passed['regime_score'].mean()
                          if 'regime_score' in df_passed.columns and len(df_passed) > 0
                          else None)
            snapshot_id = write_panel_snapshot(
                snap_conn, asof=asof_today,
                market_env=market_env,
                regime_score=float(avg_regime) if avg_regime is not None else None,
                config_dict=cfg_dict,
                config_yaml_text=cfg_yaml,
                notes=f"run_v7_backtest mode={summary['mode']} workers={args.workers}",
                project_root=ROOT,
            )
            print(f"\n✓ panel snapshot: {snapshot_id}")


if __name__ == "__main__":
    main()
