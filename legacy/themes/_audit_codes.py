"""一次性 audit: 验证 ai_revenue_manual.json 里的 code 与 iFinD 真实名称是否对应."""
import os, json, sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass

ROOT = Path(__file__).resolve().parents[1]

def _load_env(p):
    if not p.exists(): return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k,_,v = line.partition("=")
        if k.strip() and k.strip() not in os.environ:
            os.environ[k.strip()] = v.strip().strip('"').strip("'")
_load_env(ROOT / "src" / "data_sources" / "ifind" / ".env")

from iFinDPy import THS_iFinDLogin, THS_BD, THS_iFinDLogout

THS_iFinDLogin(os.environ["IFIND_USERNAME"], os.environ["IFIND_PASSWORD"])

samples = json.loads((ROOT / "themes" / "ai_revenue_manual.json").read_text(encoding="utf-8"))["samples"]

def hk4(c):
    if not c or "." not in c: return c
    d,_,s = c.partition(".")
    d = d.lstrip("0") or "0"
    if len(d) < 4: d = d.zfill(4)
    return f"{d}.{s}"

code_map = {hk4(s["code"]): (s["code"], s["name"]) for s in samples}
codes_4d = ",".join(code_map.keys())

# 探测可用名称指标
candidates = [
    "ths_stock_short_name_stock",
    "ths_security_short_name_stock",
    "ths_stock_short_name",
    "sec_name",
    "ths_stock_chinese_name_stock",
    "thscode",
]
found_ind = None
for ind in candidates:
    r = THS_BD(codes_4d, ind, "")
    if getattr(r, "errorcode", -1) == 0 and r.data is not None and len(r.data) > 0:
        # 找含名称字段
        nonempty_col = None
        for col in r.data.columns:
            if col == "thscode": continue
            if r.data[col].notna().any():
                nonempty_col = col; break
        if nonempty_col:
            found_ind = (ind, nonempty_col, r.data); break

if not found_ind:
    print("⚠ 找不到名称指标, 尝试 THS_BasicData via THS_DR")
    from iFinDPy import THS_DR
    r = THS_DR("p00102", f"thscode={codes_4d};", "thscode:Y,security_name:Y", "")
    print("THS_DR ec=", getattr(r, "errorcode", -99))
    if getattr(r, "errorcode", -1) == 0:
        print(r.data)
else:
    ind, col, df = found_ind
    print(f"使用指标: {ind} → {col}\n")
    print(f"{'code':12} {'标注名':20} {'iFinD 真实名':30} 状态")
    print("-" * 75)
    for _, row in df.iterrows():
        c4 = row["thscode"]
        orig, claimed = code_map.get(c4, (c4, "?"))
        actual = row[col]
        actual_s = str(actual) if actual is not None else ""
        # 中文名简化匹配
        norm_claim = claimed.replace("-W","").replace("-P","").strip()
        norm_actual = actual_s.replace("-W","").replace("-P","").strip()
        ok = bool(norm_actual) and (norm_claim[:2] in norm_actual or norm_actual[:2] in norm_claim)
        mark = "✓" if ok else "⚠ MISMATCH"
        print(f"{orig:12} {claimed:20} {actual_s:30} {mark}")

THS_iFinDLogout()
