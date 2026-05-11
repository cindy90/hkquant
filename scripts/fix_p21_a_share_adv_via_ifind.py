"""
P2.1 数据接入: 通过 iFinD THS_BD 拉 A+H 股的 A 股近 60 个交易日日均成交额.

写入 ipo_master.a_share_adv_cny (CNY 元), 喂给 PostAdjustments.ah_hedge tier
做 high/mid/low 分桶 (200M / 50M CNY 阈值).

iFinD 接口:
    THS_BD(a_share_code, 'ths_daily_avg_amt_int_stock', '<start>,<end>')
    - a_share_code: 已是 ifind 格式 (e.g. '300750.SZ' / '603296.SH')
    - 窗口: pricing_date - 90 calendar days .. pricing_date - 1 (≈ 60 个交易日)
    - 返回: 单值 (CNY 元), 区间日均
    - 适用范围 (per IFIND doc): 沪深 / 港股 / 全球 / 基金 / 指数 / 期货 / 现货

为何按 pricing_date 而不是 listing_date:
    pricing_date 是 NACS 评估时点 (招股定价日), as-of 设计要求所有特征都对齐到此前.
    用 listing_date 会引入定价后到上市间的市场反应, 算未来信息.

用法:
    python scripts/fix_p21_a_share_adv_via_ifind.py --dry-run
    python scripts/fix_p21_a_share_adv_via_ifind.py
    python scripts/fix_p21_a_share_adv_via_ifind.py --limit 3   # 先跑 3 只验证
    python scripts/fix_p21_a_share_adv_via_ifind.py --only 3296.HK
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "nacs_real.db"

sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

WINDOW_DAYS = 90        # 自然日窗口, ≈ 60 个交易日
RELOGIN_EVERY_N = 20    # 每 N 次调用重 login 一次
SLEEP_SEC = 0.5         # API 限速


def relogin() -> None:
    from src.data_sources.ifind import market_env_fetcher as mef
    mef._LOGIN_OK = False
    mef.login_ifind()


def _fetch_one(a_share_code: str, start_iso: str, end_iso: str) -> float | None:
    """单只 A 股拉日均成交额. 返回 CNY 元 (float) 或 None."""
    from iFinDPy import THS_BD
    # IFIND 区间指标: 第三个参数 = '<start>,<end>' (单 indicator 的逗号分隔参数)
    # 日期格式: YYYY-MM-DD
    params = f"{start_iso},{end_iso}"
    r = THS_BD(a_share_code, "ths_daily_avg_amt_int_stock", params)
    if r.errorcode != 0 or r.data is None:
        return None
    df = r.data
    if df.empty:
        return None
    # 单股调用返回 1 行 DataFrame
    val = df.iloc[0].get("ths_daily_avg_amt_int_stock")
    try:
        v = float(val)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _a_share_ipo_date(a_share_code: str) -> date | None:
    """查 A 股上市日 (返回 date 或 None). 用于识别反向 A+H (A 后于 H 上市)."""
    from iFinDPy import THS_BD
    r = THS_BD(a_share_code, "ths_ipo_date_stock", "")
    if r.errorcode != 0 or r.data is None or r.data.empty:
        return None
    val = r.data.iloc[0].get("ths_ipo_date_stock")
    if not val or not isinstance(val, str) or len(val) < 8:
        return None
    try:
        return date(int(val[:4]), int(val[4:6]), int(val[6:8]))
    except (TypeError, ValueError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0,
                    help="只处理前 N 只 (调试用)")
    ap.add_argument("--only", type=str, default="",
                    help="只处理单只 (e.g. 3296.HK)")
    args = ap.parse_args()

    if not DB.exists():
        print(f"DB 不存在: {DB}")
        return 2

    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    # 校验列在 (migration v4 必须先跑)
    cols = {r[1] for r in cur.execute("PRAGMA table_info(ipo_master)").fetchall()}
    if "a_share_adv_cny" not in cols:
        print("ipo_master.a_share_adv_cny 列不存在; 先跑 migrate_features_v4.py")
        return 3

    where = "is_a_h=1 AND a_share_code IS NOT NULL AND pricing_date IS NOT NULL"
    if args.only:
        where += f" AND stock_code = '{args.only}'"
    sql = f"SELECT ipo_id, stock_code, a_share_code, pricing_date FROM ipo_master WHERE {where}"
    rows = cur.execute(sql).fetchall()
    if args.limit:
        rows = rows[: args.limit]
    print(f"[step1] 候选 A+H deal 数: {len(rows)}")

    if not rows:
        print("无可处理 deal")
        conn.close()
        return 0

    relogin()
    print("[step2] iFinD 登录 OK")

    updates = []   # (adv, ipo_id)
    samples = []
    n_null = 0
    n_reverse_ah = 0   # 反向 A+H: A 股晚于 H 股上市, 评估时点 A 股不存在
    t0 = time.time()
    for idx, (ipo_id, hk_code, a_code, pricing_date_str) in enumerate(rows):
        if idx > 0 and idx % RELOGIN_EVERY_N == 0:
            relogin()

        pd_d = date.fromisoformat(str(pricing_date_str)[:10])
        end_d = pd_d - timedelta(days=1)
        start_d = end_d - timedelta(days=WINDOW_DAYS)

        # 反向 A+H guard: A 股若晚于 H 股 pricing 才上市, 窗口里没有数据可拉.
        # 不浪费 API call, 直接标 reverse_ah 并保 ADV=None (模型由
        # a_share_short_borrowable=0 短路, ADV NULL 不影响 NACS).
        a_ipo = _a_share_ipo_date(a_code)
        if a_ipo and a_ipo > end_d:
            n_reverse_ah += 1
            updates.append((None, ipo_id))
            print(f"  [{idx+1}/{len(rows)}] {hk_code} ({a_code}) "
                  f"reverse-A+H: A 股 {a_ipo} 晚于 H 股 pricing {pd_d}, 跳过")
            time.sleep(SLEEP_SEC)
            continue

        try:
            adv = _fetch_one(a_code, start_d.isoformat(), end_d.isoformat())
        except Exception as e:
            print(f"  [{idx+1}/{len(rows)}] {hk_code} ({a_code}) 异常: {e}; 重登重试")
            time.sleep(2)
            relogin()
            try:
                adv = _fetch_one(a_code, start_d.isoformat(), end_d.isoformat())
            except Exception as e2:
                print(f"    ✗ 重试仍失败: {e2}")
                adv = None

        updates.append((adv, ipo_id))
        if adv is None:
            n_null += 1
            print(f"  [{idx+1}/{len(rows)}] {hk_code} ({a_code}) {start_d}..{end_d} -> None")
        else:
            samples.append(adv)
            print(f"  [{idx+1}/{len(rows)}] {hk_code} ({a_code}) {start_d}..{end_d} "
                  f"-> {adv/1e6:.1f}M CNY")
        time.sleep(SLEEP_SEC)

    print(f"\n[step3] 拉取完成: {len(samples)}/{len(rows)} 成功, "
          f"reverse_ah={n_reverse_ah}, "
          f"耗时 {time.time()-t0:.1f}s")

    if samples:
        s = sorted(samples)
        n = len(s)
        # tier 分布预览
        n_high = sum(1 for v in s if v >= 2e8)
        n_mid = sum(1 for v in s if 5e7 <= v < 2e8)
        n_low = sum(1 for v in s if v < 5e7)
        print(f"  ADV 分布 (M CNY): min={s[0]/1e6:.1f} p10={s[n//10]/1e6:.1f} "
              f"p50={s[n//2]/1e6:.1f} p90={s[(n*9)//10]/1e6:.1f} max={s[-1]/1e6:.1f}")
        print(f"  tier: high(≥200M)={n_high} mid(50-200M)={n_mid} "
              f"low(<50M)={n_low} unknown={n_null}")

    if args.dry_run:
        print("\n[dry-run] 不写库")
        conn.close()
        return 0

    cur.executemany(
        "UPDATE ipo_master SET a_share_adv_cny=? WHERE ipo_id=?",
        updates,
    )
    conn.commit()

    n_filled = cur.execute(
        "SELECT COUNT(*) FROM ipo_master "
        "WHERE is_a_h=1 AND a_share_adv_cny IS NOT NULL"
    ).fetchone()[0]
    print(f"\n[step4] DB 写入完成. ipo_master.a_share_adv_cny 非 NULL: "
          f"{n_filled} 行 (A+H total = "
          f"{cur.execute('SELECT COUNT(*) FROM ipo_master WHERE is_a_h=1').fetchone()[0]})")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
