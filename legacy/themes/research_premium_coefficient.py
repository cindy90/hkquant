"""
research_premium_coefficient.py — 一次性研究脚本 (港股 AI 镀金溢价系数)

核心问题:
    华勤这种公司只有 5% AI 收入, 但市场可能给 50% 估值溢价.
    "AI 占比 X%" → "估值溢价 Y%" 这个映射函数长什么样? 拟合出来.

样本:
    过去 2 年 (~2023-05 ~ 2026-05) 港股新上市 + 部分老股, 标注 AI 业务占比.
    数据源: themes/ai_revenue_manual.json (招股书/年报手工标注, needs_review=true 的不进回归).

度量"估值溢价" (v2: 改用 PS_TTM 替代 PE_TTM):
    premium = (target_ps / peer_median_ps) - 1
    target_ps = 该样本当前 PS_TTM (市销率)
    peer_median_ps = 同行业 (peer_industry) 非 AI 概念股 PS 中位数

    (v1 用 PE_TTM 的问题: 港股 AI 概念股大量亏损 → PE 失真;
     PS_TTM 只要有正收入即可计算, 样本可比性大幅提升)

拟合:
    AI 占比 (x ∈ [0, 1]) → 溢价 (y, 一般 [0, 2.0])
    使用分段 + 二次/对数 拟合 — 因为有"边际递增" + "饱和"两段:
      • 0 < x < 0.10: 镀金阶段 (市场给极高边际溢价 — 故事 > 业绩)
      • 0.10 < x < 0.50: 验证阶段 (溢价随 AI 占比线性上行)
      • x > 0.50: 纯 AI 公司, 溢价饱和

输出:
    themes/premium_curve.json — 给 nacs_checklist_tool.html 的 JS 直接读
        {
          "fitted_at": "...",
          "n_samples": ...,
          "model": "piecewise_log_linear",
          "params": {"a": ..., "b": ..., "c": ...},
          "lookup_table": [{"ai_pct": 0.05, "premium": 0.42}, ...],
          "samples": [...]
        }

用法:
    python themes/research_premium_coefficient.py
    python themes/research_premium_coefficient.py --no-fetch    # 用上次缓存的数据重新拟合
    python themes/research_premium_coefficient.py --dry-run     # 不调 iFinD
"""
from __future__ import annotations

import sys
import os
import json
import math
import argparse
from pathlib import Path
from datetime import datetime, date, timedelta
from typing import Any, Optional

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PROJECT_ROOT = Path(__file__).resolve().parents[1]
THEMES_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

def _load_env(p: Path):
    if not p.exists(): return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, _, v = line.partition("=")
        k = k.strip(); v = v.strip().strip('"').strip("'")
        if k and k not in os.environ: os.environ[k] = v
_load_env(PROJECT_ROOT / "src" / "data_sources" / "ifind" / ".env")

MANUAL_PATH = THEMES_DIR / "ai_revenue_manual.json"
CURVE_PATH = THEMES_DIR / "premium_curve.json"
CACHE_PATH = THEMES_DIR / "_premium_research_cache.json"  # 中间数据缓存

# 行业对标 — 每个 peer_industry → 一组非 AI 同行 PE_TTM 中位数代表样本
# (人工挑选的 "纯主业 / 无 AI 标签" 港股, 用于做 baseline)
PEER_BASELINE_CODES: dict[str, list[str]] = {
    # v3 (2026-05-08, PS_TTM 口径): 剔除 PS 极端 baseline (02038 PS 0.43, 01478 PS 0.47 过低)
    "汽车半导体":   ["00981.HK"],                     # 中芯国际 PS≈8.1
    "出行平台":     ["09961.HK"],                     # 携程 PS≈4.3
    "CXO/AI 制药":  ["02359.HK"],                     # 药明康德 PS≈7.4
    "晶圆代工":     ["00981.HK"],                     # 中芯国际
    "PC/服务器":    ["02038.HK"],                     # 富智康 (代工 proxy, PS≈0.43 与联想 0.25 同量级)
    "通信设备":     ["02038.HK"],                     # 港股纯通信 peer 稀缺, 用代工 proxy
    "EMS/精密制造": ["02018.HK"],                     # 瑞声 PS≈1.3 (剔除 PS 异常低的富智康)
    "EMS/连接器":   ["02018.HK"],                     # 同上
    "光学/EMS":     ["02382.HK"],                     # 舜宇 PS≈1.5
    "光学元件":     ["02018.HK"],                     # 用瑞声替代丘钛 (01478 PS 0.47 与舜宇 1.5 不可比)
    "精密元件":     ["02382.HK"],                     # 舜宇
    "互联网内容":   ["02877.HK"],                     # 新华文轩 PS≈2.2
    "互联网应用":   ["00700.HK"],                     # 腾讯 PS≈5.1
    "在线教育":     ["00700.HK"],
    "企业 SaaS":    ["00772.HK"],                     # 阅文 PS≈3.4
    "软件":         ["00772.HK"],
    "SaaS/营销":    ["00268.HK"],                     # 金蝶 PS≈4.3
    "新能源车":     ["00175.HK"],                     # 吉利 PS≈0.6
    "创新药":       ["02269.HK"],                     # 药明生物 PS≈5.6
    "CXO":          ["02359.HK"],                     # 药明康德
    "家电":         ["02018.HK"],
    "新能源车-纯":  ["00175.HK"],
    # v3 扩样本 (2026-05-08): 加矿业 + AI 软件平台 baseline
    "矿业":         ["03993.HK"],                     # 洛阳钼业 (纯矿业 baseline)
    "AI 软件平台":  ["00772.HK"],                     # 阅文 (作为非 AI 软件锚点, 纯 AI 应显示大幅溢价)
    # v4 扩样本 (2026-05-08): 18C 纯 AI 标的进入回归
    # 这些行业全是 AI, 没有"行业内非 AI baseline", 用最接近的硬科技/互联网 anchor
    "AI 大模型":    ["00700.HK"],                     # 腾讯 PS≈5.1 (互联网应用 anchor)
    "AI 芯片":      ["00981.HK"],                     # 中芯国际 PS≈8.1 (半导体 anchor)
    "机器人":       ["00981.HK"],                     # 中芯国际 (高端制造 anchor; 港股纯机器人 baseline 稀缺)
    "AI SaaS":      ["00772.HK"],                     # 阅文 PS≈3.4 (软件 anchor)
}


# ============================================================================
# iFinD 拉 PE_TTM
# ============================================================================
def login_ifind():
    from iFinDPy import THS_iFinDLogin
    user = os.environ.get("IFIND_USERNAME"); pwd = os.environ.get("IFIND_PASSWORD")
    code = THS_iFinDLogin(user, pwd)
    if code not in (0, -201):
        raise RuntimeError(f"login fail {code}")

def logout_ifind():
    try:
        from iFinDPy import THS_iFinDLogout
        THS_iFinDLogout()
    except Exception: pass


def _hk_4d(c: str) -> str:
    """5 位港股代码 → 4 位 (THS_BD pe_ttm 接口要求)."""
    if not c or "." not in c: return c
    digits, _, suffix = c.partition(".")
    digits = digits.lstrip("0") or "0"
    if len(digits) < 4: digits = digits.zfill(4)
    return f"{digits}.{suffix}"


def fetch_ps_for_codes(codes: list[str], asof: date) -> dict[str, Optional[float]]:
    """v2 — 取 PS_TTM (市销率) 替代 PE_TTM, 解决港股亏损股 PE 失真问题.
    THS_BD(4位code, 'ps_ttm', 'YYYY-MM-DD,100')
    """
    from iFinDPy import THS_BD
    out = {c: None for c in codes}
    if not codes: return out
    code_map = {_hk_4d(c): c for c in codes}   # 4位 → 原 5 位
    r = THS_BD(",".join(code_map.keys()), "ps_ttm", f"{asof.strftime('%Y-%m-%d')},100")
    if getattr(r, "errorcode", -1) != 0 or r.data is None: return out
    df = r.data
    code_col = next((c for c in df.columns if str(c).lower() in ("thscode","ths_code","code")), None)
    val_col  = next((c for c in df.columns if "ps" in str(c).lower()), None)
    if not code_col or not val_col: return out
    for _, row in df.iterrows():
        c4 = str(row[code_col]).strip()
        orig = code_map.get(c4) or code_map.get(_hk_4d(c4))
        if not orig: continue
        try:
            v = float(row[val_col])
            if not math.isnan(v) and 0 < v < 100:   # PS 异常上限 100x (新股可能 50-80x)
                out[orig] = v
        except Exception: pass
    return out


# ============================================================================
# 数据准备
# ============================================================================
def load_samples() -> list[dict]:
    payload = json.loads(MANUAL_PATH.read_text(encoding="utf-8"))
    return [s for s in payload.get("samples", []) if not s.get("needs_review", False)]


def collect_ps_data(samples: list[dict], asof: date, *, dry_run: bool) -> dict:
    """拉所有需要的 PS_TTM (样本 + baseline). 返回 {code: ps}."""
    sample_codes = [s["code"] for s in samples]
    peer_codes = sorted({c for codes in PEER_BASELINE_CODES.values() for c in codes})
    all_codes = sorted(set(sample_codes + peer_codes))

    if dry_run:
        # mock: 给一些示例值
        return {c: 1.0 + (hash(c) % 10) / 5 for c in all_codes}

    # iFinD 批量上限按 50 切片
    out = {}
    for i in range(0, len(all_codes), 50):
        chunk = all_codes[i:i+50]
        m = fetch_ps_for_codes(chunk, asof)
        out.update(m)
    return out


def compute_premiums(samples: list[dict], ps_map: dict[str, float]) -> list[dict]:
    """对每个样本计算 premium = sample_ps / peer_median_ps - 1."""
    rows = []
    for s in samples:
        code = s["code"]
        peer_industry = s.get("peer_industry", "")
        sample_ps = ps_map.get(code)
        peer_codes = PEER_BASELINE_CODES.get(peer_industry, [])
        peer_pses = [ps_map.get(c) for c in peer_codes if ps_map.get(c) and ps_map.get(c) != sample_ps]
        peer_pses = [v for v in peer_pses if v is not None]
        peer_median = (sorted(peer_pses)[len(peer_pses)//2]
                       if len(peer_pses) > 0 else None)
        if not sample_ps or not peer_median:
            rows.append({**s, "sample_ps": sample_ps, "peer_median_ps": peer_median,
                         "premium": None, "skip_reason": "no_ps_or_no_peer"})
            continue
        premium = sample_ps / peer_median - 1
        # 离群剔除: |premium| > 5 (放宽 — AI 镀金溢价真实可达 +200~+400%, 不应被当噪声)
        if abs(premium) > 5:
            rows.append({**s, "sample_ps": sample_ps, "peer_median_ps": peer_median,
                         "premium": premium, "skip_reason": "outlier"})
            continue
        rows.append({**s, "sample_ps": sample_ps, "peer_median_ps": peer_median,
                     "premium": premium, "skip_reason": None})
    return rows


# ============================================================================
# 拟合
# ============================================================================
def fit_curve(rows: list[dict]) -> dict:
    """
    拟合 AI 占比 (x) → 溢价 (y) 的曲线.

    用对数函数 + 边际递减形态:
        y = a * log(1 + b*x) + c
    可同时表达"低占比阶段陡 (镀金)" + "高占比饱和".
    用 numpy 拟合最小二乘 (scipy 可选, 但 numpy 已够).
    """
    import numpy as np
    valid = [r for r in rows if r.get("premium") is not None and r.get("skip_reason") is None]
    if len(valid) < 5:
        # 样本太少, 给一条经验曲线 (人工给定常数 — 用户可后续校正)
        return {
            "fitted": False,
            "model": "manual_default_2026",
            "params": {"a": 0.7, "b": 8.0, "c": 0.0},
            "n_used": len(valid),
            "note": "样本不足 (<5), 使用经验默认曲线: premium = 0.7 * log(1 + 8*x). 0% AI → 0; 5% AI → 0.24; 30% AI → 0.84; 100% AI → 1.54",
        }

    xs = np.array([r["ai_revenue_pct"] for r in valid], dtype=float)
    ys = np.array([r["premium"] for r in valid], dtype=float)

    # 网格搜索 b ∈ [1, 30], 对每个 b 用线性 LSQ 解 a, c
    best = None
    for b in np.linspace(0.5, 30, 60):
        feat = np.log(1.0 + b * xs)         # ϕ(x)
        A = np.column_stack([feat, np.ones_like(feat)])
        try:
            sol, _, _, _ = np.linalg.lstsq(A, ys, rcond=None)
            a, c = float(sol[0]), float(sol[1])
            pred = a * feat + c
            ssr = float(np.sum((pred - ys) ** 2))
        except Exception:
            continue
        if best is None or ssr < best["ssr"]:
            best = {"a": a, "b": float(b), "c": c, "ssr": ssr}
    if best is None:
        return {
            "fitted": False, "model": "fit_failed",
            "params": {"a": 0.7, "b": 8.0, "c": 0.0},
            "n_used": len(valid), "note": "拟合失败回退默认值",
        }

    # R^2
    ymean = float(np.mean(ys))
    sst = float(np.sum((ys - ymean) ** 2))
    r2 = 1.0 - best["ssr"] / sst if sst > 0 else None

    return {
        "fitted": True,
        "model": "log_linear: y = a * log(1 + b*x) + c",
        "params": {"a": best["a"], "b": best["b"], "c": best["c"]},
        "n_used": len(valid),
        "r_squared": r2,
        "note": ("0% AI 收入仍可能有非零截距 (c), 反映港股整体 AI 主题情绪; "
                 "拟合用网格搜索 b + 线性解 a,c, 最小化 SSR."),
    }


def make_lookup_table(curve: dict) -> list[dict]:
    """生成 0-100% 的查询表, 给前端直接用."""
    p = curve["params"]; a, b, c = p["a"], p["b"], p["c"]
    rows = []
    for pct_int in range(0, 101, 5):
        x = pct_int / 100.0
        y = a * math.log(1.0 + b * x) + c
        y = max(-0.5, min(3.0, y))   # clamp
        rows.append({"ai_pct": x, "premium": round(y, 4)})
    return rows


# ============================================================================
# 主流程
# ============================================================================
def main():
    p = argparse.ArgumentParser()
    p.add_argument("--date", type=str)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-fetch", action="store_true",
                   help="跳过 iFinD 拉取, 用 _premium_research_cache.json 重新拟合")
    args = p.parse_args()

    today = (datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else date.today())
    samples = load_samples()
    print(f"加载样本 {len(samples)} 只 (排除 needs_review)")

    if args.no_fetch and CACHE_PATH.exists():
        cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        ps_map = cache.get("ps_map") or cache.get("pe_map")  # 兼容 v1 缓存
        rows = cache["rows"]
        print(f"  已用缓存 {CACHE_PATH}")
    else:
        if not args.dry_run:
            login_ifind()
        try:
            ps_map = collect_ps_data(samples, today, dry_run=args.dry_run)
        finally:
            if not args.dry_run:
                logout_ifind()
        rows = compute_premiums(samples, ps_map)
        CACHE_PATH.write_text(
            json.dumps({"as_of": today.strftime("%Y-%m-%d"), "ps_map": ps_map, "rows": rows},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    n_valid = sum(1 for r in rows if r.get("premium") is not None and r.get("skip_reason") is None)
    print(f"\n样本溢价计算完成 (PS_TTM 口径): {n_valid}/{len(rows)} 有效")
    for r in sorted(rows, key=lambda x: x.get("ai_revenue_pct", 0)):
        if r.get("premium") is None:
            print(f"  [skip] {r['code']} {r['name']}: {r.get('skip_reason')}")
            continue
        tag = " [outlier]" if r.get("skip_reason") == "outlier" else ""
        print(f"  AI={r['ai_revenue_pct']:.0%}  premium={r['premium']:+.2%}  | {r['code']} {r['name']}  (PS {r['sample_ps']:.2f} vs peer {r['peer_median_ps']:.2f}){tag}")

    curve = fit_curve(rows)
    print(f"\n拟合结果: model={curve['model']}, n={curve['n_used']}, params={curve['params']}")
    if "r_squared" in curve and curve["r_squared"] is not None:
        print(f"  R² = {curve['r_squared']:.3f}")

    lookup = make_lookup_table(curve)
    out = {
        "fitted_at": datetime.now().isoformat(),
        "as_of_data": today.strftime("%Y-%m-%d"),
        "n_samples_total": len(rows),
        "n_samples_used": curve["n_used"],
        "model": curve["model"],
        "params": curve["params"],
        "r_squared": curve.get("r_squared"),
        "note": curve.get("note", ""),
        "lookup_table": lookup,
        "samples": rows,
    }
    if not args.dry_run:
        CURVE_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[output] {CURVE_PATH}")
    print("\n查询表样例:")
    for r in lookup[:6]:
        print(f"  AI {r['ai_pct']:.0%} → 溢价 {r['premium']:+.1%}")
    print(f"  ...")
    print(f"  AI 100% → 溢价 {lookup[-1]['premium']:+.1%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
