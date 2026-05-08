"""
build_ipo_returns_cache.py — 历史港股 IPO 30 日收益缓存

读 data/raw/ifind/ifind_ipo_info.csv 拿全部 IPO 列表,
对每只调 THS_HistoryQuotes 拉上市后 30 个交易日行情,
算 d30_return = close[d30] / open[d1] - 1, 写入
data/derived/ipo_d30_returns.csv.

输出供 fetch_hk_market_data.py 的 regime_score 字段使用.

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
    # 期望列 p05310_f001=代码 f002=简称 f003=上市日期
    df_ipo = df_ipo.rename(columns={
        "p05310_f001": "thscode",
        "p05310_f002": "name",
        "p05310_f003": "listing_date_raw",
    })
    df_ipo["listing_date"] = df_ipo["listing_date_raw"].map(parse_listing_date)
    df_ipo = df_ipo[df_ipo["listing_date"].notna()].copy()
    df_ipo = df_ipo[df_ipo["thscode"].astype(str).str.endswith(".HK")].copy()
    df_ipo = df_ipo[~df_ipo["thscode"].astype(str).str.contains("_")]  # 过滤副牌
    df_ipo = df_ipo.sort_values("listing_date").reset_index(drop=True)
    print(f"  共 {len(df_ipo)} 只港股 IPO 待处理 ({df_ipo['listing_date'].min()} ~ {df_ipo['listing_date'].max()})")

    # 已有缓存
    existing: dict[str, dict] = {}
    if OUTPUT.exists() and not args.force:
        df_old = pd.read_csv(OUTPUT, encoding="utf-8-sig")
        for _, row in df_old.iterrows():
            existing[str(row["thscode"])] = {
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

    print(f"  本次需拉取 {len(todo)} 只\n")
    n_ok, n_fail = 0, 0
    for i, row in todo.iterrows():
        code_str = str(row["thscode"])
        list_dt = row["listing_date"]
        # 拉上市后 60 个自然日 (覆盖 30 个交易日 + 节假日 buffer)
        sdate = list_dt.strftime("%Y-%m-%d")
        edate = (list_dt + timedelta(days=60)).strftime("%Y-%m-%d")

        record = {
            "thscode": code_str,
            "name": row.get("name"),
            "listing_date": list_dt.isoformat(),
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
                tbl = r["tables"][0].get("table", {}) if isinstance(r, dict) else {}
                closes = [float(x) for x in (tbl.get("close") or []) if x is not None]
                opens = [float(x) for x in (tbl.get("open") or []) if x is not None]
                record["n_days"] = len(closes)
                if closes and opens:
                    record["d1_open"] = opens[0]
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
            print(f"    [{len(rows):3}/{len(todo)}] {code_str:9} {list_dt} "
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
    df_out = df_out[["thscode", "name", "listing_date", "d1_open", "d30_close",
                     "return_d30", "n_days", "status"]]
    df_out = df_out.sort_values("listing_date").reset_index(drop=True)
    df_out.to_csv(OUTPUT, index=False, encoding="utf-8-sig")

    print(f"\n  ✓ 写入 {OUTPUT}")
    print(f"  本次 ok={n_ok} fail={n_fail}; 总缓存 {len(df_out)} 行")
    n_full = (df_out["status"] == "ok").sum()
    print(f"  完整 30d 数据 {n_full} 只 / 部分 {(df_out['status'].astype(str).str.startswith('partial')).sum()} 只")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
