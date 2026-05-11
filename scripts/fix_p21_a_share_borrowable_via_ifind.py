"""
P2.1 数据接入: 通过 iFinD THS_BD 拉 A+H 股的 A 股是否两融标的.

写入 ipo_master.a_share_short_borrowable (1=是 / 0=否 / NULL=未知), 喂给
PostAdjustments.ah_hedge 触发判断 (is_a_h AND a_share_short_borrowable).

iFinD 接口:
    THS_BD(a_share_code, 'ths_is_mt_ss_underlying_stock', '<date>')
    - a_share_code: ifind 格式 (e.g. '300750.SZ' / '603296.SH')
    - date: 单日 YYYY-MM-DD
    - 返回: 1=是 / 0=否
    - 适用范围 (per IFIND doc): 沪深 / 基金 (港股不适用; 我们查 A 股代码)

为何按 pricing_date - 1d:
    NACS 评估时点 = pricing_date (招股定价日). 融券资格可能动态变化,
    取定价前一日的状态最贴近建模时点的真实可对冲性.

修哪个 bug:
    run_v7_backtest.py 历史上把 a_share_short_borrowable 写死等于 is_a_h
    (即"只要是 A+H 就当可融券"). 这是错误简化 — 创业板/科创板新股以及部分
    主板股票都不是两融标的. 接入此数据后, 不可融券的 A+H deal 不再触发
    ah_hedge 加成 (而是用基础值不打 ×1.0X 的乘子).

用法:
    python scripts/fix_p21_a_share_borrowable_via_ifind.py --dry-run
    python scripts/fix_p21_a_share_borrowable_via_ifind.py
    python scripts/fix_p21_a_share_borrowable_via_ifind.py --limit 3
    python scripts/fix_p21_a_share_borrowable_via_ifind.py --only 3296.HK
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

RELOGIN_EVERY_N = 20
SLEEP_SEC = 0.5


def relogin() -> None:
    from src.data_sources.ifind import market_env_fetcher as mef
    mef._LOGIN_OK = False
    mef.login_ifind()


def _fetch_one(a_share_code: str, asof_iso: str) -> int | None:
    """单只 A 股拉两融标的状态. 返回 1=是 / 0=否 / None=拉不到.

    IFIND 实测返回中文字符串 "是" / "否" (probe 见 commit msg),
    也兼容罕见的数值返回.
    """
    from iFinDPy import THS_BD
    r = THS_BD(a_share_code, "ths_is_mt_ss_underlying_stock", asof_iso)
    if r.errorcode != 0 or r.data is None:
        return None
    df = r.data
    if df.empty:
        return None
    val = df.iloc[0].get("ths_is_mt_ss_underlying_stock")
    if val is None:
        return None
    if isinstance(val, str):
        s = val.strip()
        if s == "是":
            return 1
        if s == "否":
            return 0
        return None
    try:
        return 1 if int(float(val)) == 1 else 0
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

    cols = {r[1] for r in cur.execute("PRAGMA table_info(ipo_master)").fetchall()}
    if "a_share_short_borrowable" not in cols:
        print("ipo_master.a_share_short_borrowable 列不存在; 先跑 migrate_features_v5.py")
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

    updates = []   # (val, ipo_id)
    n_yes = 0
    n_no = 0
    n_null = 0
    t0 = time.time()
    for idx, (ipo_id, hk_code, a_code, pricing_date_str) in enumerate(rows):
        if idx > 0 and idx % RELOGIN_EVERY_N == 0:
            relogin()

        pd_d = date.fromisoformat(str(pricing_date_str)[:10])
        asof_d = pd_d - timedelta(days=1)
        try:
            val = _fetch_one(a_code, asof_d.isoformat())
        except Exception as e:
            print(f"  [{idx+1}/{len(rows)}] {hk_code} ({a_code}) 异常: {e}; 重登重试")
            time.sleep(2)
            relogin()
            try:
                val = _fetch_one(a_code, asof_d.isoformat())
            except Exception as e2:
                print(f"    ✗ 重试仍失败: {e2}")
                val = None

        updates.append((val, ipo_id))
        if val is None:
            n_null += 1
            tag = "?"
        elif val == 1:
            n_yes += 1
            tag = "可融券"
        else:
            n_no += 1
            tag = "不可融券"
        print(f"  [{idx+1}/{len(rows)}] {hk_code} ({a_code}) @{asof_d} -> {val} ({tag})")
        time.sleep(SLEEP_SEC)

    print(f"\n[step3] 拉取完成: yes={n_yes} / no={n_no} / null={n_null}, "
          f"耗时 {time.time()-t0:.1f}s")

    if args.dry_run:
        print("\n[dry-run] 不写库")
        conn.close()
        return 0

    cur.executemany(
        "UPDATE ipo_master SET a_share_short_borrowable=? WHERE ipo_id=?",
        updates,
    )
    conn.commit()

    n_filled = cur.execute(
        "SELECT COUNT(*) FROM ipo_master "
        "WHERE is_a_h=1 AND a_share_short_borrowable IS NOT NULL"
    ).fetchone()[0]
    n_total = cur.execute(
        "SELECT COUNT(*) FROM ipo_master WHERE is_a_h=1"
    ).fetchone()[0]
    print(f"\n[step4] DB 写入完成. ipo_master.a_share_short_borrowable 非 NULL: "
          f"{n_filled}/{n_total} (A+H total)")

    # 进一步: 不可融券的 deal 现在不再触发 ah_hedge (相对旧行为是缩水)
    n_no_db = cur.execute(
        "SELECT COUNT(*) FROM ipo_master "
        "WHERE is_a_h=1 AND a_share_short_borrowable=0"
    ).fetchone()[0]
    if n_no_db > 0:
        print(f"\n  ⚠ {n_no_db} 只 A+H 的 A 股不在两融名单 — 这些 deal 的 ah_hedge")
        print(f"     不再触发, NACS 相对旧行为下降 5-10%")
        for r in cur.execute("""
            SELECT stock_code, a_share_code, company_name_zh
            FROM ipo_master
            WHERE is_a_h=1 AND a_share_short_borrowable=0
            ORDER BY stock_code
        """):
            print(f"     {r[0]:8s} {r[1]:12s} {r[2] or ''}")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
