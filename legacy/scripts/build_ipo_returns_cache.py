"""
build_ipo_returns_cache.py — 历史港股 IPO 30 日收益缓存

读 data/raw/ifind/ifind_ipo_info.csv 拿全部 IPO 列表,
对每只调 THS_HistoryQuotes 拉上市后 30 个交易日行情,
算 d30_return = close[d30] / open[d1] - 1, 写入
data/derived/ipo_d30_returns.csv.

输出供 fetch_hk_market_data.py 的 regime_score 字段使用.

字段语义:
    apply_date   — p05310_f003 原始招股/招股截止日 (非挂牌日)
    listing_date — closes 数组首个交易日 = 真挂牌首交易日
                   (THS_HistoryQuotes 自动跳过非交易日, closes[0] 一定是真首日)
    apply_listing_lag_days — listing_date - apply_date, 异常 (>7 天) 时数据可疑

用法:
    python scripts/build_ipo_returns_cache.py            # 增量 (跳过已有)
    python scripts/build_ipo_returns_cache.py --force    # 全量重建
    python scripts/build_ipo_returns_cache.py --limit 5  # 只测前 5 只
"""
from __future__ import annotations

import sys
import os
import argparse
import time
from pathlib import Path
from datetime import datetime, timedelta

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# .env
def _load_env(p: Path) -> None:
    if not p.exists():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_load_env(PROJECT_ROOT / "src" / "data_sources" / "ifind" / ".env")

import pandas as pd

IPO_CSV = PROJECT_ROOT / "data" / "raw" / "ifind" / "ifind_ipo_info.csv"
OUTPUT = PROJECT_ROOT / "data" / "derived" / "ipo_d30_returns.csv"


def parse_listing_date(s: str):
    s = str(s)[:10]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            continue
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="忽略已有缓存全量重建")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 只 (调试用)")
    ap.add_argument("--sleep", type=float, default=0.3, help="每只间隔 (秒)")
    args = ap.parse_args()

    if not IPO_CSV.exists():
        print(f"❌ IPO 源 CSV 不存在: {IPO_CSV}")
        print("   先跑 src/data_sources/ifind/full_data_pull.py 生成")
        return 2

    df_ipo = pd.read_csv(IPO_CSV, encoding="utf-8-sig")
    # 期望列 p05310_f001=代码 f002=简称 f003=招股/招股截止日 (非挂牌日!)
    df_ipo = df_ipo.rename(columns={
        "p05310_f001": "thscode",
        "p05310_f002": "name",
        "p05310_f003": "apply_date_raw",
    })
    df_ipo["apply_date"] = df_ipo["apply_date_raw"].map(parse_listing_date)
    df_ipo = df_ipo[df_ipo["apply_date"].notna()].copy()
    df_ipo = df_ipo[df_ipo["thscode"].astype(str).str.endswith(".HK")].copy()
    df_ipo = df_ipo[~df_ipo["thscode"].astype(str).str.contains("_")]  # 过滤副牌
    df_ipo = df_ipo.sort_values("apply_date").reset_index(drop=True)
    print(f"  共 {len(df_ipo)} 只港股 IPO 待处理 (apply_date {df_ipo['apply_date'].min()} ~ {df_ipo['apply_date'].max()})")

    # 已有缓存
    existing: dict[str, dict] = {}
    if OUTPUT.exists() and not args.force:
        df_old = pd.read_csv(OUTPUT, encoding="utf-8-sig")
        for _, row in df_old.iterrows():
            existing[str(row["thscode"])] = {
                "apply_date": str(row.get("apply_date") or ""),
                "apply_listing_lag_days": row.get("apply_listing_lag_days"),
                "listing_date": str(row["listing_date"]),
                "return_d30": row.get("return_d30"),
                "d1_open": row.get("d1_open"),
                "d30_close": row.get("d30_close"),
                "name": row.get("name"),
                "n_days": row.get("n_days"),
                "status": row.get("status"),
            }
        print(f"  增量模式: 已有 {len(existing)} 只缓存, 只补缺失")

    # 登录
    from iFinDPy import THS_iFinDLogin, THS_iFinDLogout, THS_HistoryQuotes
    user, pwd = os.environ.get("IFIND_USERNAME", ""), os.environ.get("IFIND_PASSWORD", "")
    code = THS_iFinDLogin(user, pwd)
    if code not in (0, -201):
        print(f"❌ 登录失败: {code}")
        return 3
    print("  ✓ iFinD 登录成功")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    todo = df_ipo if args.force else df_ipo[~df_ipo["thscode"].astype(str).isin(existing.keys())]
    if args.limit > 0:
        todo = todo.head(args.limit)

    today_dt = datetime.now().date()
    print(f"  本次需拉取 {len(todo)} 只\n")
    n_ok, n_fail = 0, 0
    for i, row in todo.iterrows():
        code_str = str(row["thscode"])
        apply_dt = row["apply_date"]
        # sdate 提前 90 天 buffer: 实测 p05310_f003 字段对历史 IPO 不可靠
        # (例: 2250.HK 小黄鸭德盈 f003=2022-02-06 但真挂牌 ≤ 2022-01-27; 0664.HK
        # f003=2026-04-30 但真挂牌 2026-03-31 lag=+30d). closes[0] 才是真挂牌首日.
        # 90 天 buffer 对真 IPO 安全 (IPO 之前无交易历史), 能覆盖大部分异常.
        sdate = (apply_dt - timedelta(days=90)).strftime("%Y-%m-%d")
        # edate 不能超过今天: iFinD 对 edate>today 的请求返回 ec=-1010 (实测确认).
        # 对最近 IPO, edate 截到 today, 数据不足 30d 时 status=partial, 后续重建会补.
        edate_dt = min(apply_dt + timedelta(days=60), today_dt)
        edate = edate_dt.strftime("%Y-%m-%d")

        record = {
            "thscode": code_str,
            "name": row.get("name"),
            "apply_date": apply_dt.isoformat(),
            "listing_date": None,         # 真挂牌首交易日, 由 closes[0] 时间戳填充
            "apply_listing_lag_days": None,
            "d1_open": None,
            "d30_close": None,
            "return_d30": None,
            "n_days": 0,
            "status": "pending",
        }
        try:
            r = THS_HistoryQuotes(code_str, "close,open", "", sdate, edate)
            ec = r.get("errorcode") if isinstance(r, dict) else getattr(r, "errorcode", -1)
            if ec != 0:
                record["status"] = f"err_{ec}"
                n_fail += 1
            else:
                if isinstance(r, dict):
                    t0 = (r.get("tables") or [{}])[0]
                    times_raw = list(t0.get("time") or [])
                    tbl = t0.get("table", {}) if isinstance(t0, dict) else {}
                else:
                    times_raw, tbl = [], {}
                # 同步过滤 None: 保持 (time, close, open) 三者索引对齐
                rc = list(tbl.get("close") or [])
                ro = list(tbl.get("open") or [])
                times: list[str] = []
                closes: list[float] = []
                opens: list[float] = []
                for j in range(max(len(rc), len(ro))):
                    c = rc[j] if j < len(rc) else None
                    o = ro[j] if j < len(ro) else None
                    if c is None or o is None:
                        continue
                    t = times_raw[j] if j < len(times_raw) else ""
                    times.append(str(t)[:10] if t is not None else "")
                    closes.append(float(c))
                    opens.append(float(o))
                record["n_days"] = len(closes)
                if closes and opens:
                    record["d1_open"] = opens[0]
                    if times:
                        record["listing_date"] = times[0]
                        try:
                            ld = datetime.strptime(times[0], "%Y-%m-%d").date()
                            record["apply_listing_lag_days"] = (ld - apply_dt).days
                        except Exception:
                            pass
                if len(closes) >= 30:
                    record["d30_close"] = closes[29]
                    record["return_d30"] = closes[29] / opens[0] - 1
                    record["status"] = "ok"
                    n_ok += 1
                elif closes:
                    record["d30_close"] = closes[-1]
                    record["return_d30"] = closes[-1] / opens[0] - 1
                    record["status"] = f"partial_{len(closes)}d"
                    n_ok += 1
                else:
                    record["status"] = "no_quote"
                    n_fail += 1
        except Exception as e:
            record["status"] = f"exc_{type(e).__name__}"
            n_fail += 1

        rows.append(record)
        if (i + 1) % 20 == 0 or len(rows) <= 5:
            lag = record["apply_listing_lag_days"]
            print(f"    [{len(rows):3}/{len(todo)}] {code_str:9} apply={apply_dt} "
                  f"listing={record['listing_date']} lag={lag} "
                  f"{record['status']:18} ret={record['return_d30']}")
        time.sleep(args.sleep)

    THS_iFinDLogout()

    # 合并 existing + 新数据
    final_records = []
    for code_str, rec in existing.items():
        rec["thscode"] = code_str
        final_records.append(rec)
    seen = set(existing.keys())
    for rec in rows:
        if rec["thscode"] in seen:
            # 覆盖
            final_records = [r for r in final_records if r["thscode"] != rec["thscode"]]
        final_records.append(rec)

    df_out = pd.DataFrame(final_records)
    # 兼容旧缓存: 没 apply_date / lag 列时补 NaN 占位
    for col, default in [
        ("apply_date", None),
        ("apply_listing_lag_days", None),
    ]:
        if col not in df_out.columns:
            df_out[col] = default
    df_out = df_out[["thscode", "name", "apply_date", "listing_date",
                     "apply_listing_lag_days", "d1_open", "d30_close",
                     "return_d30", "n_days", "status"]]
    df_out = df_out.sort_values("listing_date").reset_index(drop=True)
    df_out.to_csv(OUTPUT, index=False, encoding="utf-8-sig")

    print(f"\n  ✓ 写入 {OUTPUT}")
    print(f"  本次 ok={n_ok} fail={n_fail}; 总缓存 {len(df_out)} 行")
    n_full = (df_out["status"] == "ok").sum()
    n_partial = (df_out['status'].astype(str).str.startswith('partial')).sum()
    print(f"  完整 30d 数据 {n_full} 只 / 部分 {n_partial} 只")
    # sanity: lag > 7 天的样本告警
    try:
        lag_col = pd.to_numeric(df_out["apply_listing_lag_days"], errors="coerce")
        suspicious = df_out[lag_col.abs() > 7]
        if len(suspicious):
            print(f"\n  ⚠ apply→listing lag > 7d 异常样本 {len(suspicious)} 只 (p05310_f003 数据源可疑):")
            for _, r in suspicious.head(10).iterrows():
                print(f"    {r['thscode']} {r.get('name','')} apply={r['apply_date']} "
                      f"listing={r['listing_date']} lag={r['apply_listing_lag_days']}d")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
