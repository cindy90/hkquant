"""verify_theme_compose.py — 单跑 fetch_themes 端到端 (Step 2 验证)"""
from __future__ import annotations
import os, sys, json
from pathlib import Path
from datetime import date

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 复用 fetch_hk_market_data 的环境加载 + 函数
from scripts.fetch_hk_market_data import (
    _load_env, _ENV_PATH, fetch_themes, RunRecorder, write_json, PROJECT_ROOT
)

_load_env(_ENV_PATH)


def main():
    user = os.environ.get("IFIND_USERNAME", "")
    pwd = os.environ.get("IFIND_PASSWORD", "")
    from iFinDPy import THS_iFinDLogin, THS_iFinDLogout
    code = THS_iFinDLogin(user, pwd)
    if code not in (0, -201):
        print(f"login failed: {code}"); return 3
    print("OK login\n")

    today = date.today()
    out_dir = PROJECT_ROOT / "daily" / today.isoformat()
    rec = RunRecorder(out_dir, dry_run=False)

    try:
        themes = fetch_themes(rec, today, dry_run=False)
    finally:
        THS_iFinDLogout()

    # 摘要打印
    print("\n=== 主题合成结果摘要 ===")
    composed_n = 0
    fallback_n = 0
    for key, entry in themes.items():
        label = entry.get("label")
        status = entry.get("proxy_status", "?")
        n_const = entry.get("composition_n_constituents")
        r60 = entry.get("ret_60d")
        r60_str = f"{r60*100:+.2f}%" if isinstance(r60, (int, float)) else str(r60)
        print(f"  {key:22s} {label:8s}  status={status:42s}  n={n_const}  60d={r60_str}")
        if status == "composed":
            composed_n += 1
        elif status == "composition_failed_using_fallback":
            fallback_n += 1

    print(f"\n汇总: composed={composed_n}  fallback={fallback_n}  total={len(themes)}")

    # 写 themes.json
    write_json(out_dir / "themes.json",
               {"as_of": today.isoformat(), "themes": themes}, dry_run=False)
    print(f"\n[OK] 写入 {out_dir / 'themes.json'}")

    # 检查成分股缓存
    cache_path = PROJECT_ROOT / "data" / "theme_constituents_cache.json"
    if cache_path.exists():
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
        n_entries = len(cache.get("constituents", {}))
        print(f"[OK] theme_constituents_cache.json — {n_entries} 个 (bkid, month) 缓存")

    return 0 if composed_n > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
