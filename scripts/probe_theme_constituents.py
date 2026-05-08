"""
probe_theme_constituents.py — 探测 iFinD 板块成分股查询接口 (Step 2 准备)

目标:
    Step 1 已经把 12 个 iv_bkid 落到 watchlist.json, 但 THS_HistoryQuotes
    不接受 iv_bkid 作行情代码 (用户已确认: 011007_305848 无效命令).
    Step 2 需要走「成分股 → 等权合成 close 序列」路径.

    本脚本探测哪个 iFinD 接口能拉「板块 ID → 成分股代码列表」,
    跑通后输出 data/theme_constituents_probe.json, 供 Step 2 决策.

候选接口 (按概率排序, 跑一个成功就停):
    1. THS_DataPool('block', 'date:YYYY-MM-DD;blockname:<iv_bkid>;blocktype:thscode',
                    'thscode:Y,thsname:Y')
    2. THS_BD(thsCodes='', indicators='ths_block_constituents', date=...)
       —— 不一定支持
    3. p00091 / 同类 p 系列编号 THS_DR — 用户文档查
    4. THS_iwencai('概念板块成分股 <name>', 'stock')

用法:
    python scripts/probe_theme_constituents.py
    python scripts/probe_theme_constituents.py --bkid 011007_305848
    python scripts/probe_theme_constituents.py --probe-all   (扫所有 watchlist)

输出:
    data/theme_constituents_probe.json    -- 哪个接口跑通了, 拉到多少成分股
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

# Windows 控制台 UTF-8
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================================
# .env 加载 (与 fetch_hk_market_data.py 同模式)
# ============================================================================
def _load_env(env_path: Path) -> None:
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env(PROJECT_ROOT / "src" / "data_sources" / "ifind" / ".env")


# ============================================================================
# 候选接口探测函数 — 每个尝试一种调用方式, 成功返回 list[code], 失败返回 None
# ============================================================================
def _introspect(r, label: str) -> None:
    """把任意 iFinD 返回对象的结构打到 stdout (用于探针)"""
    t = type(r).__name__
    print(f"  [{label}] type={t}")
    if isinstance(r, dict):
        print(f"  [{label}] dict keys={list(r.keys())}")
        for k in ("errorcode", "errmsg", "tables", "data"):
            if k in r:
                v = r[k]
                if isinstance(v, list) and v:
                    print(f"  [{label}] {k}=list[{len(v)}], [0] keys={list(v[0].keys()) if isinstance(v[0], dict) else type(v[0]).__name__}")
                elif hasattr(v, "shape"):
                    print(f"  [{label}] {k}=DataFrame shape={v.shape} cols={list(v.columns)[:6]}")
                else:
                    s = str(v)[:120]
                    print(f"  [{label}] {k}={s}")
    elif hasattr(r, "__dict__"):
        attrs = [a for a in dir(r) if not a.startswith("_")][:10]
        print(f"  [{label}] attrs={attrs}")
        for a in ("errorcode", "errmsg"):
            if hasattr(r, a):
                print(f"  [{label}] {a}={getattr(r, a)}")
        if hasattr(r, "data"):
            d = getattr(r, "data")
            if hasattr(d, "shape"):
                print(f"  [{label}] data DataFrame shape={d.shape} cols={list(d.columns)[:6]}")
            else:
                print(f"  [{label}] data type={type(d).__name__}")


def _extract_codes_from_any(r) -> Optional[list[str]]:
    """从任意 iFinD 返回里尝试抠出股票代码列表"""
    # 形态 A: 对象, .data 是 DataFrame
    if hasattr(r, "data"):
        df = r.data
        if df is not None and hasattr(df, "columns") and len(df) > 0:
            for col in df.columns:
                cl = str(col).lower()
                if "thscode" == cl or cl.endswith(".thscode") or cl == "code":
                    codes = [str(x) for x in df[col].dropna().tolist()]
                    if codes and any("." in c or len(c) >= 5 for c in codes):
                        return codes
            # 兜底取第 1 列
            codes = [str(x) for x in df.iloc[:, 0].dropna().tolist()]
            if codes and any("." in c for c in codes[:3]):
                return codes
    # 形态 B: dict
    if isinstance(r, dict):
        tables = r.get("tables") or []
        if tables and isinstance(tables[0], dict):
            t0 = tables[0]
            tbl = t0.get("table", {})
            if isinstance(tbl, dict):
                for col in tbl.values():
                    if isinstance(col, list) and col and isinstance(col[0], str) and "." in col[0]:
                        return [str(x) for x in col if x]
    return None


def _try_datapool_block(bkid: str, asof: date) -> Optional[list[str]]:
    """尝试 THS_DataPool('block', ...). 板块成分股的常见接口."""
    try:
        from iFinDPy import THS_DataPool
    except ImportError:
        print("  [skip] THS_DataPool 未在 iFinDPy 中找到")
        return None
    edate = asof.strftime("%Y-%m-%d")
    # 多种 param 变体 + 多种 datatype 变体
    attempts = [
        # (datatype, params, indicator)
        ("block", f"blockname:{bkid};date:{edate}", "thscode,security_name"),
        ("block", f"date:{edate};blockname:{bkid};blocktype:thscode", "thscode:Y,thsname:Y"),
        ("block", f"blockname:{bkid}", "thscode"),
        ("block", bkid, "thscode"),  # 最简
        ("blockconstituent", f"blockname:{bkid};date:{edate}", "thscode"),
        ("blocknew", f"blockname:{bkid};date:{edate}", "thscode"),
    ]
    for i, (dt, params, ind) in enumerate(attempts):
        try:
            r = THS_DataPool(dt, params, ind)
            label = f"DataPool[{i}]/{dt}/{params[:40]}"
            _introspect(r, label)
            codes = _extract_codes_from_any(r)
            if codes:
                print(f"  ✓ [DataPool[{i}]] 拿到 {len(codes)} 只代码, sample={codes[:3]}")
                return codes
        except Exception as e:
            print(f"  [DataPool[{i}]/{dt}] 异常: {type(e).__name__}: {e}")
    return None


def _try_dr_p00091(bkid: str, asof: date) -> Optional[list[str]]:
    """尝试 THS_DR('p00091', ...). 部分 iFinD 文档里 p00091 是板块成分查询."""
    try:
        from iFinDPy import THS_DR
    except ImportError:
        return None
    candidate_codes = ["p00091", "p03570", "p03571"]  # 常见板块成分查询编号
    for pcode in candidate_codes:
        try:
            r = THS_DR(
                pcode,
                f"iv_bkid={bkid}",
                f"{pcode}_f001:Y,{pcode}_f002:Y,{pcode}_f003:Y",
                "format:dataframe",
            )
            ec = getattr(r, "errorcode", None)
            print(f"  [DR/{pcode}] ec={ec} errmsg={getattr(r, 'errmsg', '')}")
            if ec == 0:
                df = getattr(r, "data", None)
                if df is not None and len(df) > 1:  # 1 行可能是板块自身, ≥2 行才像成分股
                    print(f"  [DR/{pcode}] 列名={list(df.columns)} 行数={len(df)}")
                    return list(df.iloc[:, 0].astype(str))
        except Exception as e:
            print(f"  [DR/{pcode}] 异常: {type(e).__name__}: {e}")
    return None


def _try_iwencai(bkid: str, label: str) -> Optional[list[str]]:
    """兜底: 用 iwencai 搜成分股 (但 bkid 直接搜可能不识别, 用 label 更可能成功)."""
    try:
        from iFinDPy import THS_iwencai
    except ImportError:
        return None
    queries = [f"{label}板块成分股", f"概念板块 {bkid} 成分股", f"{label} 港股"]
    for q in queries:
        try:
            r = THS_iwencai(q, "stock")
            ec = getattr(r, "errorcode", None)
            print(f"  [iwencai/{q!r}] ec={ec}")
            if ec == 0:
                df = getattr(r, "data", None)
                if df is not None and len(df) > 0:
                    return list(df.iloc[:, 0].astype(str))[:200]  # 限 200 防爆
        except Exception as e:
            print(f"  [iwencai/{q!r}] 异常: {type(e).__name__}: {e}")
    return None


# ============================================================================
# 主流程
# ============================================================================
def probe_one(bkid: str, label: str, asof: date) -> dict:
    """对一个 bkid 跑全部候选接口, 记录第一个成功的"""
    print(f"\n=== 探测 {bkid} ({label}) ===")
    result: dict[str, Any] = {
        "bkid": bkid,
        "label": label,
        "probed_at": datetime.now().isoformat(),
        "winner": None,
        "constituents_sample": [],
        "n_constituents": 0,
        "attempts": [],
    }
    for name, fn in [
        ("DataPool_block", lambda: _try_datapool_block(bkid, asof)),
        ("DR_p00091_family", lambda: _try_dr_p00091(bkid, asof)),
        ("iwencai", lambda: _try_iwencai(bkid, label)),
    ]:
        result["attempts"].append(name)
        try:
            codes = fn()
        except Exception as e:
            print(f"  [{name}] 顶层异常: {type(e).__name__}: {e}")
            traceback.print_exc()
            codes = None
        if codes:
            result["winner"] = name
            result["n_constituents"] = len(codes)
            result["constituents_sample"] = codes[:10]
            print(f"  ✓ {name} 拉到 {len(codes)} 只: {codes[:5]}...")
            break
    if not result["winner"]:
        print(f"  ✗ 所有候选接口均失败")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="探测 iFinD 板块成分股查询接口")
    parser.add_argument("--bkid", default=None,
                        help="只探测一个 bkid (例如 011007_305848). 默认从 watchlist 取首个")
    parser.add_argument("--probe-all", action="store_true",
                        help="扫描 watchlist 里所有 bkid (慢, 仅在确认 winner 接口后用)")
    parser.add_argument("--label", default="人工智能", help="bkid 对应主题名 (用于 iwencai 查询)")
    args = parser.parse_args()

    # 登录
    user = os.environ.get("IFIND_USERNAME", "")
    pwd = os.environ.get("IFIND_PASSWORD", "")
    if not user or not pwd:
        print("❌ 未读到 IFIND_USERNAME / IFIND_PASSWORD")
        return 2
    from iFinDPy import THS_iFinDLogin, THS_iFinDLogout
    code = THS_iFinDLogin(user, pwd)
    if code not in (0, -201):
        print(f"❌ iFinD 登录失败: {code}")
        return 3
    print("✓ iFinD 登录成功\n")

    today = date.today()
    results: list[dict] = []

    try:
        wl_path = PROJECT_ROOT / "data" / "watchlist.json"
        if args.probe_all and wl_path.exists():
            wl = json.loads(wl_path.read_text(encoding="utf-8"))
            themes = wl.get("themes_to_track", {})
            for key, meta in themes.items():
                bkid = meta.get("iv_bkid")
                if bkid:
                    results.append(probe_one(bkid, meta.get("label", key), today))
        else:
            bkid = args.bkid or "011007_305848"
            label = args.label
            if not args.bkid and wl_path.exists():
                wl = json.loads(wl_path.read_text(encoding="utf-8"))
                themes = wl.get("themes_to_track", {})
                for key, meta in themes.items():
                    if meta.get("iv_bkid") == bkid:
                        label = meta.get("label", label)
                        break
            results.append(probe_one(bkid, label, today))
    finally:
        THS_iFinDLogout()

    out_path = PROJECT_ROOT / "data" / "theme_constituents_probe.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({
            "probed_at": datetime.now().isoformat(),
            "n_probed": len(results),
            "winners": {r["bkid"]: r["winner"] for r in results},
            "results": results,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n✓ 探测结果: {out_path}")

    # 摘要
    n_ok = sum(1 for r in results if r["winner"])
    print(f"\n摘要: {n_ok}/{len(results)} 个 bkid 拉到了成分股")
    if n_ok > 0:
        winners = {r["winner"] for r in results if r["winner"]}
        print(f"成功的接口: {winners}")
    return 0 if n_ok > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
