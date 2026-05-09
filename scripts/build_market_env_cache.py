"""
P1-#6 修复: 用 iFinD 真实数据重灌 market_environment_cache.

背景:
    market_environment_cache 全 54 行 source='fallback', 4 个 iFinD 字段
    (hsi_60d_return / vol / vol_pct_rank / valuation_pct) 全是常量, 让
    Regime Gate / Q_ecosystem 计算退化为常数, factor 信号被吃掉.

策略:
    1. 删除所有 source='fallback' 的旧行 (保留 ifind/json 的)
    2. 复用 dao.fetch_market_env_at(allow_ifind=True), 它内部会:
       - 优先查 cache (skip)
       - 失败走 fallback
    3. 因为 iFinD 长跑会被踢, 每 N 月重新登录一次
    4. 失败行允许跳过 (保留 fallback), 后续可重跑

用法:
    python scripts/build_market_env_cache.py --start 2022-01 --end 2026-05
    python scripts/build_market_env_cache.py --start 2022-01 --end 2026-05 --dry-run
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

# 强制 utf-8 输出 (中文 print)
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def month_iter(start: str, end: str):
    """yield 月初 date 对象, [start, end] 闭区间, 格式 YYYY-MM"""
    sy, sm = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    y, m = sy, sm
    while (y, m) <= (ey, em):
        yield date(y, m, 1)
        m += 1
        if m == 13:
            m = 1
            y += 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2022-01")
    ap.add_argument("--end", default="2026-05")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--relogin-every", type=int, default=12,
                    help="每 N 个月重新登录一次 iFinD (防止长跑被踢)")
    args = ap.parse_args()

    if not DB.exists():
        print(f"DB 不存在: {DB}")
        return 2

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    # 1) 清掉 fallback 行 (保留 ifind/json)
    n_fallback = conn.execute(
        "SELECT COUNT(*) FROM market_environment_cache WHERE source='fallback'"
    ).fetchone()[0]
    n_keep = conn.execute(
        "SELECT COUNT(*) FROM market_environment_cache WHERE source!='fallback'"
    ).fetchone()[0]
    print(f"[step1] 现有 cache: fallback={n_fallback}, ifind/json={n_keep}")

    if not args.dry_run and n_fallback > 0:
        conn.execute("DELETE FROM market_environment_cache WHERE source='fallback'")
        conn.commit()
        print(f"[step1] 已删除 {n_fallback} 行 fallback")

    # 2) 提前登录 iFinD (失败立刻退出)
    from data_sources.ifind.market_env_fetcher import login_ifind
    try:
        login_ifind()
        print("[step2] iFinD 首次登录 OK")
    except Exception as e:
        print(f"[step2] iFinD 登录失败: {e}")
        conn.close()
        return 3

    # 3) 逐月 fetch
    from data.dao import fetch_market_env_at

    months = list(month_iter(args.start, args.end))
    print(f"[step3] 待处理 {len(months)} 个月: {args.start} ~ {args.end}")

    if args.dry_run:
        print("[dry-run] 不写库, 退出")
        conn.close()
        return 0

    ok = fail = skip = 0
    for i, m in enumerate(months):
        # 每 N 月重新登录, 防被踢
        if i > 0 and i % args.relogin_every == 0:
            try:
                # 强制 re-login: reset 全局 _LOGIN_OK 标志
                import data_sources.ifind.market_env_fetcher as mef
                mef._LOGIN_OK = False
                login_ifind()
                print(f"  [re-login {i}] OK")
            except Exception as e:
                print(f"  [re-login {i}] 失败: {e}, 跳过本月")
                skip += 1
                continue

        try:
            env = fetch_market_env_at(conn, m, allow_ifind=True)
            # 验证写入的 source
            row = conn.execute(
                "SELECT source FROM market_environment_cache WHERE asof_month=?",
                (m.isoformat(),)
            ).fetchone()
            src = row["source"] if row else "?"
            if src == "ifind":
                ok += 1
                print(f"  {m.isoformat()}: ifind OK "
                      f"hsi_ret={env.hsi_60d_return:.4f} "
                      f"vol={env.hsi_60d_vol_annualized:.4f} "
                      f"pe_pct={env.hsi_valuation_pct:.3f}")
            else:
                fail += 1
                print(f"  {m.isoformat()}: source={src} (ifind 失败)")
        except Exception as e:
            fail += 1
            print(f"  {m.isoformat()}: EXC {type(e).__name__}: {e}")

    print(f"\n[done] ok={ok} fail={fail} skip={skip}")

    # 4) 验证
    print("\n--- 验证 ---")
    sources = conn.execute(
        "SELECT source, COUNT(*) FROM market_environment_cache GROUP BY source"
    ).fetchall()
    for src, n in sources:
        print(f"  {src}: {n}")
    n_const = conn.execute(
        "SELECT COUNT(DISTINCT hsi_60d_return) FROM market_environment_cache"
    ).fetchone()[0]
    print(f"  hsi_60d_return unique: {n_const} (修复前 = 1)")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
