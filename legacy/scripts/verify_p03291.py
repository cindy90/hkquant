"""
verify_p03291.py — 用用户给的精确格式验证 THS_DR('p03291', ...) 能拉成分股

用户原话:
    THS_DR('p03291',
           'date=20260508;blockname=011007_309171;iv_type=allcontract',
           'p03291_f001:Y,p03291_f002:Y,p03291_f003:Y,p03291_f004:Y',
           'format:dataframe')

跑一遍, 把每个字段语义打印出来, 写到 data/theme_constituents_probe.json
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_env(env_path: Path) -> None:
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


_load_env(PROJECT_ROOT / "src" / "data_sources" / "ifind" / ".env")


def main() -> int:
    user = os.environ.get("IFIND_USERNAME", "")
    pwd = os.environ.get("IFIND_PASSWORD", "")
    if not user or not pwd:
        print("[X] 未读到 IFIND 凭据")
        return 2

    from iFinDPy import THS_iFinDLogin, THS_iFinDLogout, THS_DR

    code = THS_iFinDLogin(user, pwd)
    if code not in (0, -201):
        print(f"[X] iFinD 登录失败: {code}")
        return 3
    print("[OK] iFinD 登录成功\n")

    # 用户给的样例 bkid + 多测一个 (确认通用)
    test_cases = [
        ("011007_309171", "半导体概念"),  # 用户原例
        ("011007_305848", "人工智能"),
        ("011007_309172", "机器人概念"),
    ]
    today = date.today().strftime("%Y%m%d")
    results = []

    try:
        for bkid, label in test_cases:
            print(f"=== {bkid} ({label}) ===")
            r = THS_DR(
                "p03291",
                f"date={today};blockname={bkid};iv_type=allcontract",
                "p03291_f001:Y,p03291_f002:Y,p03291_f003:Y,p03291_f004:Y",
                "format:dataframe",
            )
            ec = getattr(r, "errorcode", None)
            em = getattr(r, "errmsg", "")
            print(f"  errorcode={ec} errmsg={em}")
            df = getattr(r, "data", None)
            entry = {
                "bkid": bkid,
                "label": label,
                "errorcode": ec,
                "errmsg": em,
                "n_rows": 0,
                "columns": [],
                "sample_rows": [],
                "extracted_codes_sample": [],
            }
            if df is not None and hasattr(df, "shape"):
                print(f"  shape={df.shape}")
                print(f"  columns={list(df.columns)}")
                entry["columns"] = list(df.columns)
                entry["n_rows"] = int(df.shape[0])
                if df.shape[0] > 0:
                    print("\n  --- head(5) ---")
                    print(df.head(5).to_string())
                    entry["sample_rows"] = df.head(5).astype(str).to_dict(orient="records")
                    # 找 ths code 列 (一般是 f001 或带 thscode 的列)
                    code_col = None
                    for c in df.columns:
                        cl = str(c).lower()
                        if "thscode" in cl or cl.endswith("_f001"):
                            code_col = c
                            break
                    if code_col is None and len(df.columns) > 0:
                        # 看哪列里包含像 .HK / .SH / .SZ 的代码
                        for c in df.columns:
                            try:
                                vals = df[c].astype(str).head(5).tolist()
                                if any(("." in v) and v.split(".")[-1].upper() in ("HK", "SH", "SZ", "BJ") for v in vals):
                                    code_col = c
                                    break
                            except Exception:
                                pass
                    if code_col is not None:
                        codes = [str(x) for x in df[code_col].dropna().tolist()]
                        print(f"\n  代码列={code_col}, n={len(codes)}, sample={codes[:5]}")
                        entry["extracted_codes_sample"] = codes[:10]
                        entry["code_column"] = code_col
                        entry["n_constituents"] = len(codes)
            results.append(entry)
            print()
    finally:
        THS_iFinDLogout()

    out = PROJECT_ROOT / "data" / "theme_constituents_probe.json"
    out.write_text(
        json.dumps(
            {
                "winner": "THS_DR_p03291",
                "format": {
                    "function": "THS_DR",
                    "p_code": "p03291",
                    "params_template": "date=YYYYMMDD;blockname=<iv_bkid>;iv_type=allcontract",
                    "indicators": "p03291_f001:Y,p03291_f002:Y,p03291_f003:Y,p03291_f004:Y",
                    "format": "format:dataframe",
                    "source": "用户提供 (THS_DR p03291 板块成分查询)",
                },
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n[OK] 写入 {out}")
    n_ok = sum(1 for r in results if r.get("n_rows", 0) > 0)
    print(f"摘要: {n_ok}/{len(results)} 个 bkid 拿到非空数据")
    return 0 if n_ok > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
