"""
P1-#6 修复 (partial 版): 绕开 PE_TTM 失败, 直接灌真实 HSI 三件套.

背景:
    fetch_market_env_dict 总报 'PE_TTM 百分位获取失败' (HSI.HK ths_pe_ttm_index
    indicator 当前 iFinD 账号拿不到), 导致整个 cache 退化 fallback. 但
    fetch_hsi_block 单独可用, fetch_southbound_normalized 可能可用.

策略:
    不动 fetcher 代码; 直接组合各 fetch 函数, PE 用 0.5 中性, 标
    source='ifind_partial' 区分真实 ifind 和 fallback.
    这样 6 个字段中:
      hsi_60d_return        : 真实
      hsi_60d_vol_annualized: 真实
      hsi_60d_vol_pct_rank  : 真实
      hsi_valuation_pct     : 0.5 (PE 缺, fallback)
      southbound_30d_net    : 真实 (尽量) / 0
      sector_60d_vol        : = HSI vol (按设计)
      hk_ipo_30d_*          : DB 算

用法:
    python scripts/build_market_env_cache_partial.py --start 2022-01 --end 2026-05
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "nacs_real.db"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def month_iter(start: str, end: str):
    sy, sm = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    y, m = sy, sm
    while (y, m) <= (ey, em):
        yield date(y, m, 1)
        m += 1
        if m == 13:
            m = 1
            y += 1


def _month_end(d: date) -> date:
    """与 dao._month_end 保持一致逻辑: 月初 → 当月最后一天"""
    if d.month == 12:
        nxt = date(d.year + 1, 1, 1)
    else:
        nxt = date(d.year, d.month + 1, 1)
    from datetime import timedelta
    return nxt - timedelta(days=1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2022-01")
    ap.add_argument("--end", default="2026-05")
    ap.add_argument("--relogin-every", type=int, default=12)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not DB.exists():
        print(f"DB 不存在: {DB}")
        return 2

    from data_sources.ifind.market_env_fetcher import (
        login_ifind, fetch_hsi_block, fetch_southbound_normalized,
    )
    import data_sources.ifind.market_env_fetcher as mef
    from data.dao import _compute_ipo_30d_stats_from_db

    try:
        login_ifind()
        print("[init] iFinD 登录 OK")
    except Exception as e:
        print(f"[init] iFinD 登录失败: {e}")
        return 3

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    # 清掉 fallback 行
    n_fb = conn.execute(
        "SELECT COUNT(*) FROM market_environment_cache WHERE source='fallback'"
    ).fetchone()[0]
    print(f"[init] 现有 fallback 行: {n_fb}")
    if not args.dry_run:
        conn.execute("DELETE FROM market_environment_cache WHERE source='fallback'")
        conn.commit()

    months = list(month_iter(args.start, args.end))
    print(f"[init] 待处理 {len(months)} 个月")

    if args.dry_run:
        # dry-run 测一个月看效果
        m = months[0]
        end_d = _month_end(m)
        try:
            hsi = fetch_hsi_block(end_d)
            sb = fetch_southbound_normalized(end_d)
            print(f"[dry-run] {m}: HSI={hsi}, southbound={sb}")
        except Exception as e:
            print(f"[dry-run] {m}: FAIL {e}")
        conn.close()
        return 0

    ok = fail = 0
    for i, m in enumerate(months):
        if i > 0 and i % args.relogin_every == 0:
            try:
                mef._LOGIN_OK = False
                login_ifind()
                print(f"  [re-login {i}] OK")
            except Exception as e:
                print(f"  [re-login {i}] FAIL: {e}")

        end_d = _month_end(m)
        try:
            hsi = fetch_hsi_block(end_d)
        except Exception as e:
            fail += 1
            print(f"  {m}: HSI FAIL {e}")
            continue

        # southbound 失败用 0
        try:
            sb = fetch_southbound_normalized(end_d)
            if sb is None:
                sb = 0.0
        except Exception:
            sb = 0.0

        # hk_ipo_30d_* 从 DB 算
        ipo_avg, ipo_brk = _compute_ipo_30d_stats_from_db(conn, m)

        conn.execute("""
            INSERT OR REPLACE INTO market_environment_cache (
                asof_month, hsi_60d_return, hsi_60d_vol_annualized, hsi_60d_vol_pct_rank,
                hsi_valuation_pct, hk_ipo_30d_avg_d30, hk_ipo_30d_breakage_rate,
                southbound_30d_net_normalized, sector_60d_vol_annualized, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            m.isoformat(),
            hsi["hsi_60d_return"],
            hsi["hsi_60d_vol_annualized"],
            hsi["hsi_60d_vol_pct_rank"],
            0.5,  # PE 中性 fallback
            ipo_avg,
            ipo_brk,
            sb,
            hsi["hsi_60d_vol_annualized"],  # sector = hsi vol
            "ifind_partial",
        ))
        conn.commit()
        ok += 1
        print(f"  {m}: OK ret={hsi['hsi_60d_return']:+.4f} "
              f"vol={hsi['hsi_60d_vol_annualized']:.3f} "
              f"vol_pct={hsi['hsi_60d_vol_pct_rank']:.3f} "
              f"sb={sb:+.3f}")

    print(f"\n[done] ok={ok} fail={fail}")

    # 验证
    print("\n--- 验证 ---")
    for src, n in conn.execute(
        "SELECT source, COUNT(*) FROM market_environment_cache GROUP BY source"
    ).fetchall():
        print(f"  {src}: {n}")
    n_ret = conn.execute(
        "SELECT COUNT(DISTINCT hsi_60d_return) FROM market_environment_cache"
    ).fetchone()[0]
    n_vol = conn.execute(
        "SELECT COUNT(DISTINCT hsi_60d_vol_annualized) FROM market_environment_cache"
    ).fetchone()[0]
    n_pct = conn.execute(
        "SELECT COUNT(DISTINCT hsi_60d_vol_pct_rank) FROM market_environment_cache"
    ).fetchone()[0]
    n_sb = conn.execute(
        "SELECT COUNT(DISTINCT southbound_30d_net_normalized) FROM market_environment_cache"
    ).fetchone()[0]
    print(f"  hsi_60d_return unique:        {n_ret}")
    print(f"  hsi_60d_vol_annualized unique: {n_vol}")
    print(f"  hsi_60d_vol_pct_rank unique:   {n_pct}")
    print(f"  southbound unique:             {n_sb}")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
