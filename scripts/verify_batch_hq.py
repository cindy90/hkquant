"""verify_batch_hq.py — 验证批量 THS_HistoryQuotes 返回结构 (Step 2 前置)"""
from __future__ import annotations
import os, sys, json
from pathlib import Path
from datetime import date, timedelta

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[1]


def _load_env(p: Path):
    if not p.exists():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip(); v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


_load_env(ROOT / "src" / "data_sources" / "ifind" / ".env")


def main():
    user = os.environ.get("IFIND_USERNAME", "")
    pwd = os.environ.get("IFIND_PASSWORD", "")
    from iFinDPy import THS_iFinDLogin, THS_iFinDLogout, THS_HistoryQuotes

    code = THS_iFinDLogin(user, pwd)
    if code not in (0, -201):
        print(f"login failed: {code}"); return 3
    print("OK login")

    today = date.today()
    sdate = (today - timedelta(days=120)).strftime("%Y-%m-%d")
    edate = today.strftime("%Y-%m-%d")

    # 半导体板块 5 只代表股
    codes = "0085.HK,0522.HK,0981.HK,1347.HK,1385.HK"
    print(f"\n=== batch THS_HistoryQuotes('{codes}', 'close', '', {sdate}, {edate}) ===")
    r = THS_HistoryQuotes(codes, 'close', '', sdate, edate)

    # 探针: 整体类型
    print(f"top type={type(r).__name__}  is_dict={isinstance(r, dict)}")
    if isinstance(r, dict):
        print(f"  dict keys={list(r.keys())}")
        print(f"  errorcode={r.get('errorcode')} errmsg={r.get('errmsg','')}")
        tables = r.get("tables") or []
        print(f"  n_tables={len(tables)}")
        for i, t in enumerate(tables[:5]):
            if isinstance(t, dict):
                tk = list(t.keys())
                print(f"  tables[{i}] keys={tk} thscode={t.get('thscode')}")
                tbl = t.get("table") if isinstance(t.get("table"), dict) else {}
                n_close = len(tbl.get('close') or [])
                n_time = len(t.get('time') or [])
                print(f"  tables[{i}].table cols={list(tbl.keys()) if tbl else '-'} n_close={n_close} n_time={n_time}")
                if n_close > 0:
                    closes = tbl.get('close') or []
                    times = t.get('time') or []
                    print(f"    head: {[(times[j], closes[j]) for j in range(min(3,n_close))]}")
        THS_iFinDLogout()
        return 0
    elif hasattr(r, "errorcode"):
        print(f"  attrs={[a for a in dir(r) if not a.startswith('_')][:15]}")
        print(f"  errorcode={getattr(r,'errorcode',None)} errmsg={getattr(r,'errmsg','')}")
        df = getattr(r, "data", None)
        if df is not None:
            print(f"  data type={type(df).__name__}")
            if hasattr(df, "shape"):
                print(f"  shape={df.shape}")
                print(f"  columns={list(df.columns)}")
                print(f"  head:\n{df.head(8).to_string()}")
                if "thscode" in df.columns:
                    print(f"  unique thscodes={df['thscode'].unique().tolist()}")
    else:
        print(f"  fallthrough: type={type(r).__name__}")

    THS_iFinDLogout()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
