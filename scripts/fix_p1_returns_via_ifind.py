"""
P1-#10 修复: 通过 iFinD 拉历史价, 补全 ipo_returns 6 个空字段.

现状:
    return_d1_close       381/384 (99.2%)
    return_d30            363/384 (94.5%)
    return_m3             347/384 (90.4%)
    return_m6             272/384 (70.8%)
    return_m12              0/384 (0.0%)   <- 待补
    return_unlock_d30       0/384 (0.0%)   <- 待补
    return_unlock_d90       0/384 (0.0%)   <- 待补
    max_drawdown_m6         0/384 (0.0%)   <- 待补
    avg_daily_volume_hkd    0/384 (0.0%)   <- 待补

口径 (基准 = d1_open, 与 data/derived/ipo_d30_returns.csv 一致):
    交易日: 21=d30, 63=m3, 126=m6, 252=m12
    return_d1_close      = closes[0] / opens[0] - 1
    return_d30           = closes[20] / opens[0] - 1
    return_m3            = closes[62] / opens[0] - 1
    return_m6            = closes[125] / opens[0] - 1
    return_m12           = closes[251] / opens[0] - 1
    return_unlock_d30    = closes[unlock_idx + 30] / closes[unlock_idx] - 1
    return_unlock_d90    = closes[unlock_idx + 90] / closes[unlock_idx] - 1
        其中 unlock_idx = 离 (listing_date + lockup_months 月) 最近的交易日索引
    max_drawdown_m6      = (opens[0] - min(lows[0:126])) / opens[0]   注意是正值 (跌幅)
    avg_daily_volume_hkd = mean(amount[0:60])                         前 60 个交易日

只补 NULL, 不覆盖已有 (避免基准混合).

防 look-ahead:
    - 所有字段是历史回算标签, 模型在 asof 推断时不直接读这些
    - peer_lockup_avg_drawdown 取自 listing_date < pricing_date 的同侪历史 max_drawdown_m6,
      要求 peer 的 listing_date < pricing_date - 6 个月 (m6 已知后才可用)

用法:
    python scripts/fix_p1_returns_via_ifind.py --dry-run --limit 5
    python scripts/fix_p1_returns_via_ifind.py
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

RELOGIN_EVERY_N = 30


def relogin():
    from src.data_sources.ifind import market_env_fetcher as mef
    mef._LOGIN_OK = False
    mef.login_ifind()


def fetch_history(stock_code: str, start: str, end: str) -> dict | None:
    """单只 THS_HistoryQuotes; 返回 {opens, highs, lows, closes, amounts, times} 或 None."""
    from iFinDPy import THS_HistoryQuotes
    indicators = "open;high;low;close;amount"
    r = THS_HistoryQuotes(stock_code, indicators, "", start, end)
    ec = r.get("errorcode") if hasattr(r, "get") else getattr(r, "errorcode", -1)
    if ec != 0:
        msg = r.get("errmsg") if hasattr(r, "get") else getattr(r, "errmsg", "")
        raise RuntimeError(f"THS_HistoryQuotes ec={ec} msg={msg}")
    tables = r.get("tables") or []
    if not tables:
        return None
    t0 = tables[0]
    tbl = t0.get("table", {}) if isinstance(t0, dict) else {}
    if not tbl:
        return None
    times = list(t0.get("time") or [])

    def _floats(key):
        v = tbl.get(key) or []
        return [None if x is None else float(x) for x in v]

    return {
        "opens": _floats("open"),
        "highs": _floats("high"),
        "lows": _floats("low"),
        "closes": _floats("close"),
        "amounts": _floats("amount"),
        "times": times,
    }


def add_months(d: date, months: int) -> date:
    """日期加月 (闰月降级到月末)."""
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    # 月末降级
    import calendar
    last = calendar.monthrange(y, m)[1]
    return date(y, m, min(d.day, last))


def find_idx_after(times: list, target: date) -> int | None:
    """times 中第一个 >= target 的索引."""
    target_s = target.isoformat()
    for i, t in enumerate(times):
        if str(t) >= target_s:
            return i
    return None


def compute_returns(hist: dict, lockup_months: int) -> dict:
    """根据历史价 dict 计算 6 个 returns 字段."""
    out = {
        "return_d1_close": None,
        "return_d30": None,
        "return_m3": None,
        "return_m6": None,
        "return_m12": None,
        "return_unlock_d30": None,
        "return_unlock_d90": None,
        "max_drawdown_m6": None,
        "avg_daily_volume_hkd": None,
    }
    opens = hist["opens"]
    closes = hist["closes"]
    lows = hist["lows"]
    amounts = hist["amounts"]
    times = hist["times"]
    n = len(times)
    if n == 0 or not opens or opens[0] is None or opens[0] == 0:
        return out
    d1_open = opens[0]

    def _ret_at(idx):
        if idx < n and closes[idx] is not None:
            return closes[idx] / d1_open - 1
        return None

    out["return_d1_close"] = _ret_at(0)
    out["return_d30"] = _ret_at(20)
    out["return_m3"] = _ret_at(62)
    out["return_m6"] = _ret_at(125)
    out["return_m12"] = _ret_at(251)

    # max_drawdown_m6 = max((d1_open - min(lows[0..126])) / d1_open, 0)
    if n >= 1:
        m6_lows = [x for x in lows[: min(126, n)] if x is not None]
        if m6_lows:
            mn = min(m6_lows)
            dd = (d1_open - mn) / d1_open
            out["max_drawdown_m6"] = max(0.0, dd)

    # avg_daily_volume_hkd: 上市后 60 个交易日均值
    if n >= 1:
        m_amts = [x for x in amounts[: min(60, n)] if x is not None]
        if m_amts:
            out["avg_daily_volume_hkd"] = sum(m_amts) / len(m_amts)

    # unlock returns
    # listing_date 取 times[0]; lockup_end = listing_date + lockup_months 个月
    try:
        ld = date.fromisoformat(str(times[0])[:10])
    except Exception:
        return out
    unlock_target = add_months(ld, lockup_months or 6)
    unlock_idx = find_idx_after(times, unlock_target)
    if unlock_idx is None or unlock_idx >= n or closes[unlock_idx] is None:
        return out
    base = closes[unlock_idx]
    if base is None or base == 0:
        return out
    if unlock_idx + 30 < n and closes[unlock_idx + 30] is not None:
        out["return_unlock_d30"] = closes[unlock_idx + 30] / base - 1
    if unlock_idx + 90 < n and closes[unlock_idx + 90] is not None:
        out["return_unlock_d90"] = closes[unlock_idx + 90] / base - 1
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 只 (调试用)")
    ap.add_argument("--only-missing", action="store_true",
                    help="跳过 ipo_returns 已有 m12 的(节流; 默认全量重算 NULL 字段)")
    args = ap.parse_args()

    if not DB.exists():
        print(f"DB 不存在: {DB}")
        return 2

    relogin()
    print("[step1] iFinD 登录 OK")

    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    rows = cur.execute("""
        SELECT m.ipo_id, m.stock_code, m.listing_date, COALESCE(m.lockup_months, 6)
        FROM ipo_master m
        WHERE m.listing_date IS NOT NULL
        ORDER BY m.listing_date
    """).fetchall()
    if args.limit:
        rows = rows[: args.limit]
    print(f"[step2] 待处理 {len(rows)} 只 IPO")

    today = date.today()
    updates = []  # [(field-tuples..., ipo_id)]
    stats = {k: 0 for k in [
        "return_d1_close", "return_d30", "return_m3", "return_m6", "return_m12",
        "return_unlock_d30", "return_unlock_d90", "max_drawdown_m6", "avg_daily_volume_hkd"
    ]}
    n_fail = 0

    t0 = time.time()
    for i, (ipo_id, sc, ld_str, lockup) in enumerate(rows):
        if i > 0 and i % RELOGIN_EVERY_N == 0:
            try:
                relogin()
            except Exception as e:
                print(f"  ⚠ relogin 失败 i={i}: {e}")
        try:
            ld = date.fromisoformat(str(ld_str)[:10])
        except Exception:
            n_fail += 1
            continue
        # 拉到 listing_date + 14 个月或今天 (取小)
        end_d = min(today, add_months(ld, 14))
        start_d = ld - timedelta(days=2)
        if end_d <= start_d:
            n_fail += 1
            continue
        try:
            hist = fetch_history(sc, start_d.isoformat(), end_d.isoformat())
        except Exception as e:
            time.sleep(1)
            try:
                relogin()
                hist = fetch_history(sc, start_d.isoformat(), end_d.isoformat())
            except Exception as e2:
                print(f"  ✗ {sc} 失败: {e2}")
                n_fail += 1
                continue
        if not hist or not hist["opens"]:
            n_fail += 1
            continue

        ret = compute_returns(hist, lockup)
        # 只统计可计算字段 (有值)
        for k, v in ret.items():
            if v is not None:
                stats[k] += 1
        updates.append((ret, ipo_id))

        # 进度 + 节流
        if (i + 1) % 20 == 0 or i == len(rows) - 1:
            sys.stdout.write(f"\r  {i+1}/{len(rows)} 已处理, 失败 {n_fail}, "
                             f"已用 {time.time()-t0:.0f}s    ")
            sys.stdout.flush()
        time.sleep(0.15)
    print()

    print(f"\n[stats] {len(updates)} 只成功, {n_fail} 失败")
    for k, n in stats.items():
        print(f"  {k:25s}: {n}")

    if args.dry_run:
        print("\n[dry-run] 不写库")
        # 抽样展示
        if updates:
            print("\n抽样:")
            for ret, ipo_id in updates[:3]:
                print(f"  {ipo_id}: m12={ret['return_m12']}, "
                      f"unlock_d30={ret['return_unlock_d30']}, "
                      f"max_dd_m6={ret['max_drawdown_m6']}, "
                      f"avg_vol={ret['avg_daily_volume_hkd']}")
        conn.close()
        return 0

    # 写库: 用 INSERT OR REPLACE 更新 ipo_returns. 只填 NULL.
    # 策略: 先查现状, 仅在原 NULL 时覆盖.
    print("\n[step3] 写库 (只填 NULL 字段, 不覆盖已有)")
    cur.execute("BEGIN")
    n_writes = 0
    for ret, ipo_id in updates:
        # 取现状
        existing = cur.execute(
            "SELECT return_d1_close, return_d30, return_m3, return_m6, return_m12, "
            "return_unlock_d30, return_unlock_d90, max_drawdown_m6, avg_daily_volume_hkd "
            "FROM ipo_returns WHERE ipo_id=?", (ipo_id,)
        ).fetchone()
        if not existing:
            # 行不存在 -> insert
            cur.execute("""
                INSERT INTO ipo_returns
                (ipo_id, return_d1_close, return_d30, return_m3, return_m6, return_m12,
                 return_unlock_d30, return_unlock_d90, max_drawdown_m6, avg_daily_volume_hkd)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (ipo_id, ret["return_d1_close"], ret["return_d30"],
                  ret["return_m3"], ret["return_m6"], ret["return_m12"],
                  ret["return_unlock_d30"], ret["return_unlock_d90"],
                  ret["max_drawdown_m6"], ret["avg_daily_volume_hkd"]))
            n_writes += 1
            continue
        # 行存在 -> 仅填 NULL
        keys = ["return_d1_close", "return_d30", "return_m3", "return_m6", "return_m12",
                "return_unlock_d30", "return_unlock_d90", "max_drawdown_m6", "avg_daily_volume_hkd"]
        sets = []
        params = []
        for j, k in enumerate(keys):
            if existing[j] is None and ret[k] is not None:
                sets.append(f"{k}=?")
                params.append(ret[k])
        if not sets:
            continue
        params.append(ipo_id)
        cur.execute(f"UPDATE ipo_returns SET {', '.join(sets)} WHERE ipo_id=?", params)
        n_writes += 1

    conn.commit()
    print(f"  写入 {n_writes} 行")

    # 验证
    print("\n--- 验证 ---")
    n_total = cur.execute("SELECT COUNT(*) FROM ipo_returns").fetchone()[0]
    for col in ["return_d1_close", "return_d30", "return_m3", "return_m6", "return_m12",
                "return_unlock_d30", "return_unlock_d90", "max_drawdown_m6",
                "avg_daily_volume_hkd"]:
        n = cur.execute(f"SELECT COUNT(*) FROM ipo_returns WHERE {col} IS NOT NULL").fetchone()[0]
        print(f"  {col:25s}: {n}/{n_total} ({n/n_total*100:.1f}%)")

    conn.close()
    print(f"\n总耗时: {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
