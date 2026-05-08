"""探针 v8: iwencai data 结构 + domain 支持范围"""
import sys, os
from pathlib import Path
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
env = PROJECT_ROOT / "src" / "data_sources" / "ifind" / ".env"
for raw in env.read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

import iFinDPy
from iFinDPy import THS_iFinDLogin, THS_iFinDLogout, THS_iwencai, THS_WC
print("login:", THS_iFinDLogin(os.environ["IFIND_USERNAME"], os.environ["IFIND_PASSWORD"]))

# 试不同 domain
for domain in ["news", "report", "abstract", "stock", "researchreport", "announcement", "公告"]:
    print(f"\n=== iwencai domain={domain!r} ===")
    try:
        r = THS_iwencai("港股 2026-05-08", domain)
        print(f"  type={type(r).__name__}")
        if isinstance(r, dict):
            print(f"  keys={list(r.keys())}  ec={r.get('errorcode')} msg={r.get('errmsg','')[:80]}")
            if r.get("tables"):
                t0 = r["tables"][0]
                print(f"  tables[0].keys={list(t0.keys()) if isinstance(t0,dict) else t0}")
        else:
            print(f"  ec={getattr(r,'errorcode','?')} msg={getattr(r,'errmsg','')[:80]}")
            if hasattr(r, "data") and r.data is not None:
                print(f"  cols={list(r.data.columns)[:10]}  rows={len(r.data)}")
                if len(r.data):
                    print(r.data.head(2).to_string()[:600])
    except Exception as e:
        print(f"  EXC {type(e).__name__}: {e}")

# 试 THS_WC (可能更稳)
print("\n=== THS_WC ===")
for domain in ["news", "report"]:
    try:
        r = THS_WC("港股新闻", domain)
        print(f"  WC domain={domain} type={type(r).__name__} ec={getattr(r,'errorcode','?')}")
        if hasattr(r, "data") and r.data is not None:
            print(f"    cols={list(r.data.columns)[:10]}  rows={len(r.data)}")
            if len(r.data):
                print(r.data.head(2).to_string()[:500])
    except Exception as e:
        print(f"  WC domain={domain} EXC {e}")

THS_iFinDLogout()
