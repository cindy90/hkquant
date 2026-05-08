"""探针 v5: p04275 港股通个股资金流向"""
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

from iFinDPy import THS_iFinDLogin, THS_iFinDLogout, THS_DR
print("login:", THS_iFinDLogin(os.environ["IFIND_USERNAME"], os.environ["IFIND_PASSWORD"]))

# === p04275 港股通个股资金流向 ===
# type=1 沪市港股通  type=2 深市港股通  type=? 合并?
print("\n=== p04275 type=2 单日 ===")
r = THS_DR(
    'p04275',
    'type=2;sdate=2026-05-07;edate=2026-05-07',
    ','.join([f'p04275_f{i:03d}:Y' for i in range(1, 13)]),
    'format:dataframe'
)
print(f"  ec={getattr(r,'errorcode','?')} msg={getattr(r,'errmsg','')[:80]}")
if hasattr(r, "data") and r.data is not None:
    df = r.data
    print(f"  cols={list(df.columns)}  rows={len(df)}")
    print(df.head(8).to_string())
    print("...")
    print(df.tail(3).to_string())

# 试 type=1
print("\n=== p04275 type=1 单日 ===")
r2 = THS_DR(
    'p04275',
    'type=1;sdate=2026-05-07;edate=2026-05-07',
    ','.join([f'p04275_f{i:03d}:Y' for i in range(1, 13)]),
    'format:dataframe'
)
print(f"  ec={getattr(r2,'errorcode','?')} msg={getattr(r2,'errmsg','')[:80]}")
if hasattr(r2, "data") and r2.data is not None:
    print(f"  rows={len(r2.data)}")
    print(r2.data.head(3).to_string())

# 试 30 日范围 — 一次拿过去 30 天
print("\n=== p04275 type=2 30 日 ===")
r3 = THS_DR(
    'p04275',
    'type=2;sdate=2026-04-08;edate=2026-05-08',
    ','.join([f'p04275_f{i:03d}:Y' for i in range(1, 13)]),
    'format:dataframe'
)
print(f"  ec={getattr(r3,'errorcode','?')} msg={getattr(r3,'errmsg','')[:80]}")
if hasattr(r3, "data") and r3.data is not None:
    print(f"  rows={len(r3.data)}")

THS_iFinDLogout()
