"""
theme_tracker.py — 港股主题情绪追踪 (服务复合业务公司估值)

每天 8:30 与 fetch_hk_market_data.py 一起跑. 流程:

  1. 加载 themes/theme_definitions.json
  2. 对每个主题:
       a. 拉板块指数 60d close (通过同花顺 iv_bkid + 等权合成 OR fallback)
       b. 拉每个核心公司的 PE_TTM (THS_BD ths_pe_ttm_index)
       c. 拉每个公司过去 7 天的研报情绪 (THS_DR p06108 研报查询, 评级映射打分)
       d. 用 Kimi 把全部信号聚合 → 0-100 热度分
  3. 追加一行到 themes/history.csv (历史趋势分析用)
  4. 输出 themes/heat_today.json (HTML 工具加载)

用法:
    # 跟主流程串起来:
    python scripts/fetch_hk_market_data.py && python themes/theme_tracker.py

    # 单跑:
    python themes/theme_tracker.py
    python themes/theme_tracker.py --date 2026-05-08 --dry-run
    python themes/theme_tracker.py --no-kimi   # 用启发式打分代替 LLM (调试用)
"""
from __future__ import annotations

import sys
import os
import csv
import json
import math
import argparse
from pathlib import Path
from datetime import datetime, date, timedelta
from typing import Any, Optional

# Windows UTF-8
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PROJECT_ROOT = Path(__file__).resolve().parents[1]
THEMES_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

# .env 加载 (复用主流程模式)
def _load_env(env_path: Path) -> None:
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip(); v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v

_load_env(PROJECT_ROOT / "src" / "data_sources" / "ifind" / ".env")
_load_env(PROJECT_ROOT / ".env")  # Kimi key 可能放这里

DEFINITIONS_PATH = THEMES_DIR / "theme_definitions.json"
HISTORY_PATH = THEMES_DIR / "history.csv"
HEAT_TODAY_PATH = THEMES_DIR / "heat_today.json"


# ============================================================================
# 数据加载
# ============================================================================
def load_definitions() -> dict[str, dict]:
    if not DEFINITIONS_PATH.exists():
        raise FileNotFoundError(f"未找到 {DEFINITIONS_PATH}")
    payload = json.loads(DEFINITIONS_PATH.read_text(encoding="utf-8"))
    return payload.get("themes", {})


# ============================================================================
# iFinD 调用 (与主流程同模式, 复用 SDK)
# ============================================================================
def login_ifind() -> None:
    """复用主流程的 ifind_login (统一登录状态, 支持 -1010 自动重连)."""
    from scripts.fetch_hk_market_data import ifind_login as _login
    user = os.environ.get("IFIND_USERNAME"); pwd = os.environ.get("IFIND_PASSWORD")
    if not user or not pwd:
        raise RuntimeError("未配置 IFIND_USERNAME / IFIND_PASSWORD")
    _login(user, pwd)


def logout_ifind() -> None:
    try:
        from scripts.fetch_hk_market_data import ifind_logout as _logout
        _logout()
    except Exception:
        pass


def _hq_unpack(result):
    """复制主流程 fetch_hk_market_data._hq_unpack 的简化版.
    THS_HistoryQuotes 返回有两种形态 (OrderedDict / 对象), 必须双形态判断."""
    class _Out:
        errorcode = -1; errmsg = "unknown"
        closes = []; values = []; times = []
    out = _Out()
    if hasattr(result, "errorcode"):
        out.errorcode = int(result.errorcode)
        out.errmsg = str(getattr(result, "errmsg", ""))
        df = getattr(result, "data", None)
        if df is not None and hasattr(df, "columns"):
            if "close" in df.columns:
                out.closes = [float(x) for x in df["close"].dropna().tolist()]
            time_col = next((c for c in df.columns if str(c).lower() in ("time","date","thsdate")), None)
            if time_col:
                out.times = df[time_col].tolist()
            indicator_cols = [c for c in df.columns
                              if c not in ("close","open","time","date","thsdate","thscode","ths_code")]
            if indicator_cols:
                col = indicator_cols[0]
                out.values = [float(x) for x in df[col].dropna().tolist() if x is not None]
        return out
    if isinstance(result, dict):
        out.errorcode = int(result.get("errorcode", -1))
        out.errmsg = str(result.get("errmsg", ""))
        tables = result.get("tables") or []
        if tables:
            t0 = tables[0]
            tbl = t0.get("table", {}) if isinstance(t0, dict) else {}
            if isinstance(tbl, dict):
                if "close" in tbl:
                    out.closes = [float(x) for x in tbl["close"] if x is not None]
                for k, v in tbl.items():
                    if k not in ("close","open") and isinstance(v, list) and v:
                        out.values = [float(x) for x in v if x is not None]
                        break
            if isinstance(t0, dict) and "time" in t0:
                out.times = list(t0["time"])
        return out
    out.errmsg = f"unknown return type: {type(result).__name__}"
    return out


def fetch_index_close_series(iv_bkid: str, fallback_code: Optional[str],
                             today: date, lookback_days: int = 120) -> tuple[list[float], str]:
    """
    单 code (HSTECH/HSCI) 拉 close 用于本地 ret 算 + 趋势可视化.
    精确板块合成 (按 iv_bkid 成分股等权) 由主流程 fetch_hk_market_data.py 完成,
    我们在 read_main_pipeline_themes() 里读它的结果, 这里只作为兜底.
    """
    from iFinDPy import THS_HistoryQuotes
    from scripts.fetch_hk_market_data import call_with_relogin
    sdate = (today - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    edate = today.strftime("%Y-%m-%d")
    code = fallback_code or "HSTECH.HK"
    r = call_with_relogin(THS_HistoryQuotes, code, "close", "", sdate, edate)
    u = _hq_unpack(r)
    if u.errorcode != 0:
        raise RuntimeError(f"拉 close 失败 code={code} ec={u.errorcode} msg={u.errmsg}")
    if not u.closes:
        raise RuntimeError(f"拉 close 失败 code={code} 空序列 (msg={u.errmsg})")
    return u.closes, code


def read_main_pipeline_themes(today: date) -> dict[str, dict]:
    """
    从主流程的 daily/<date>/themes.json 读已经按成分股合成的 ret_*.
    主流程比我们更精确 (按 iv_bkid 拉成分股 + 等权合成). 优先使用.
    主流程未跑或字段缺失时, 返回空 dict, 上层走 fallback (HSTECH 等).
    """
    p = PROJECT_ROOT / "daily" / today.strftime("%Y-%m-%d") / "themes.json"
    if not p.exists():
        return {}
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
        return payload.get("themes", {})
    except Exception:
        return {}


def _hk_code_4digit(c: str) -> str:
    """
    港股代码归一化: iFinD THS_BD('pe_ttm', ...) 实测要 4 位格式 ('0700.HK'),
    项目其他地方常用 5 位 ('00700.HK'). 自动去掉前导 0, 保证最少 4 位.
    """
    if not c or "." not in c:
        return c
    digits, _, suffix = c.partition(".")
    digits = digits.lstrip("0") or "0"
    if len(digits) < 4:
        digits = digits.zfill(4)
    return f"{digits}.{suffix}"


def fetch_pe_ttm(codes: list[str], asof: date) -> dict[str, Optional[float]]:
    """
    港股个股 PE_TTM. 实测可用 API: THS_BD(codes, 'pe_ttm', 'YYYY-MM-DD,100')
    其中 codes 必须是 4 位港股代码 ('0700.HK'). 5 位 ('00700.HK') 会 -4210.
    100 = 合并报表口径.

    返回 {原 code (5 位): pe}. 失败为 None (不抛, 上层降级).
    """
    from iFinDPy import THS_BD
    from scripts.fetch_hk_market_data import call_with_relogin
    out: dict[str, Optional[float]] = {c: None for c in codes}
    if not codes:
        return out
    # 5 位 → 4 位映射
    code_map = {_hk_code_4digit(c): c for c in codes}
    codes_4d = ",".join(code_map.keys())
    try:
        r = call_with_relogin(THS_BD, codes_4d, "pe_ttm", f"{asof.strftime('%Y-%m-%d')},100")
        ec = getattr(r, "errorcode", -1)
        if ec != 0 or not hasattr(r, "data") or r.data is None:
            return out
        df = r.data
        code_col = next((c for c in df.columns if str(c).lower() in ("thscode","ths_code","code")), None)
        val_col = next((c for c in df.columns if "pe" in str(c).lower()), None)
        if not code_col or not val_col:
            return out
        for _, row in df.iterrows():
            c4 = str(row[code_col]).strip()
            orig = code_map.get(c4) or code_map.get(_hk_code_4digit(c4))
            if not orig:
                continue
            try:
                v = float(row[val_col])
                if not math.isnan(v) and 0 < v < 500:
                    out[orig] = v
            except Exception:
                pass
    except Exception:
        pass
    return out


def fetch_research_reports(code: str, today: date, lookback_days: int = 7) -> list[dict]:
    """
    THS_DR p06108 研报查询 (公司研报). 港股研报字段不一定齐全, 失败返回空列表 (上层降级).
    返回每条 {title, rating, target_price, publish_date, institution}.
    """
    from iFinDPy import THS_DR
    from scripts.fetch_hk_market_data import call_with_relogin
    sdate = (today - timedelta(days=lookback_days)).strftime("%Y%m%d")
    edate = today.strftime("%Y%m%d")
    try:
        r = call_with_relogin(
            THS_DR,
            "p06108",
            f"thscode={code};sdate={sdate};edate={edate}",
            "p06108_f001:Y,p06108_f002:Y,p06108_f003:Y,p06108_f004:Y,p06108_f005:Y,p06108_f006:Y,p06108_f007:Y,p06108_f008:Y",
            "format:dataframe",
        )
        if getattr(r, "errorcode", -1) != 0 or r.data is None or len(r.data) == 0:
            return []
        df = r.data
        recs = []
        for _, row in df.iterrows():
            recs.append({
                "publish_date": str(row.get("p06108_f001", "")),
                "title":        str(row.get("p06108_f002", "")),
                "institution":  str(row.get("p06108_f003", "")),
                "rating":       str(row.get("p06108_f004", "")),
                "target_price": str(row.get("p06108_f005", "")),
            })
        return recs
    except Exception:
        return []


# ============================================================================
# 信号聚合
# ============================================================================
def compute_returns(closes: list[float]) -> dict[str, Optional[float]]:
    def r(n):
        if len(closes) <= n:
            return None
        return closes[-1] / closes[-1 - n] - 1
    return {"ret_5d": r(5), "ret_20d": r(20), "ret_60d": r(60)}


def rate_to_score(rating: str) -> Optional[float]:
    """券商评级 → 0-1 数值. 中文/英文兼容."""
    if not rating:
        return None
    s = str(rating).lower().strip()
    mapping = {
        "买入": 1.0, "增持": 0.8, "推荐": 0.9, "强烈推荐": 1.0,
        "中性": 0.5, "持有": 0.5, "观望": 0.5,
        "减持": 0.2, "卖出": 0.0, "回避": 0.1,
        "buy": 1.0, "outperform": 0.85, "overweight": 0.8,
        "neutral": 0.5, "hold": 0.5, "market perform": 0.5,
        "underperform": 0.2, "sell": 0.0, "underweight": 0.2,
    }
    for k, v in mapping.items():
        if k in s:
            return v
    return None


def aggregate_research_sentiment(reports_per_code: dict[str, list[dict]]) -> dict[str, Any]:
    """跨核心公司聚合研报情绪. 返回 {n_reports, avg_rating_score, n_buy, n_neutral, n_sell}."""
    n = 0; sum_score = 0.0; n_buy = 0; n_neu = 0; n_sell = 0
    for _, recs in reports_per_code.items():
        for rec in recs:
            sc = rate_to_score(rec.get("rating", ""))
            if sc is None:
                continue
            n += 1
            sum_score += sc
            if sc >= 0.7: n_buy += 1
            elif sc >= 0.4: n_neu += 1
            else: n_sell += 1
    avg = (sum_score / n) if n > 0 else None
    return {"n_reports": n, "avg_rating_score": avg,
            "n_buy": n_buy, "n_neutral": n_neu, "n_sell": n_sell}


def heuristic_heat_score(ret_5d, ret_20d, ret_60d, pe_ttm_avg, research) -> float:
    """
    fallback 启发式 (Kimi 不可用时使用): 综合动量 + PE 分位 + 研报情绪.
    输出 0-100. 与 Kimi 提示词的尺度对齐, 这样 history.csv 长期可比.
    """
    score = 50.0
    # 动量贡献 (主要权重)
    if ret_5d is not None:   score += min(max(ret_5d * 100 * 1.5, -10), 15)
    if ret_20d is not None:  score += min(max(ret_20d * 100 * 0.8, -15), 20)
    if ret_60d is not None:  score += min(max(ret_60d * 100 * 0.4, -10), 15)
    # PE 分贡献 — 高 PE 说明已经热, 适度加分
    if pe_ttm_avg is not None:
        if pe_ttm_avg > 60:    score += 10
        elif pe_ttm_avg > 35:  score += 5
        elif pe_ttm_avg < 15:  score -= 5
    # 研报情绪
    if research.get("avg_rating_score") is not None:
        score += (research["avg_rating_score"] - 0.5) * 20
    if research.get("n_reports", 0) >= 5:
        score += 3  # 研报覆盖密度高也是热度信号
    return float(max(0.0, min(100.0, score)))


# ============================================================================
# Kimi LLM 聚合
# ============================================================================
KIMI_SYSTEM_PROMPT = """你是港股二级市场情绪分析师, 服务于"复合业务公司估值"决策 (核心痛点: 像华勤这种公司 5% AI 收入但市场可能给 50% AI 估值溢价, 需要量化主题热度).

输入: 一个港股主题在当日的多维信号 (板块动量 / 核心公司 PE / 研报情绪).
任务: 输出该主题的"热度分" (0-100 整数).

打分准则 (严格遵守, 长期可比):
  • 0-20  极度冷淡 — 板块持续下跌, 研报降级, PE 处于历史低位
  • 20-40 冷淡    — 板块震荡偏弱, 研报中性
  • 40-60 中性    — 板块横盘, 多空平衡
  • 60-80 热门    — 板块上涨, 研报多 buy, PE 抬升
  • 80-100 过热   — 板块单边大涨, PE 处于历史高位, 几乎全 buy 评级 (此时镀金溢价最严重)

只输出严格 JSON: {"heat_score": <int>, "reason": "<≤80 字>", "warning": "<over_heated|under_heated|null>"}
不要任何 markdown / 代码块标记 / 其他文本.
"""


def call_kimi_score(theme_id: str, label: str, signals: dict) -> dict:
    """
    用 Kimi (OpenAI SDK 兼容) 聚合多维信号 → 热度分.
    失败时上层 fallback 到 heuristic_heat_score.
    """
    from openai import OpenAI
    api_key = os.environ.get("KIMI_API_KEY")
    if not api_key:
        raise RuntimeError("未配置 KIMI_API_KEY")
    # 兼容两种键名: KIMI_URL (本项目 .env 实际用) / KIMI_BASE_URL (官方文档命名)
    base_url = (os.environ.get("KIMI_URL")
                or os.environ.get("KIMI_BASE_URL")
                or "https://api.moonshot.cn/v1")
    model = os.environ.get("KIMI_MODEL") or "kimi-k2.6"
    client = OpenAI(api_key=api_key, base_url=base_url)

    user_msg = (
        f"主题: {label} (theme_id={theme_id})\n"
        f"信号:\n{json.dumps(signals, ensure_ascii=False, indent=2)}"
    )
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": KIMI_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.2,
        max_tokens=300,
    )
    content = resp.choices[0].message.content.strip()
    # 容错: 偶尔 LLM 会包 ```json
    if content.startswith("```"):
        content = content.strip("`")
        if content.startswith("json"):
            content = content[4:].strip()
    parsed = json.loads(content)
    score = int(parsed.get("heat_score", 50))
    score = max(0, min(100, score))
    return {
        "heat_score": score,
        "reason": parsed.get("reason", ""),
        "warning": parsed.get("warning"),
        "source": "kimi",
    }


# ============================================================================
# 主流程
# ============================================================================
def run(today: date, *, dry_run: bool = False, use_kimi: bool = True) -> dict:
    print(f"[{datetime.now().isoformat()}] theme_tracker 启动 (today={today})")
    themes = load_definitions()
    print(f"  加载主题数: {len(themes)}")

    if not dry_run:
        login_ifind()

    # 优先复用主流程合成结果 (daily/<date>/themes.json) — 避免多 theme 共用 HSTECH 粗代理
    pipeline_themes = read_main_pipeline_themes(today) if not dry_run else {}
    if pipeline_themes:
        n_composed = sum(1 for v in pipeline_themes.values()
                         if (v or {}).get("proxy_status") == "composed")
        print(f"  主流程结果: {n_composed}/{len(pipeline_themes)} composed (优先复用)")

    out: dict[str, Any] = {"as_of": today.strftime("%Y-%m-%d"), "themes": {}}

    try:
        for theme_id, meta in themes.items():
            label = meta.get("label", theme_id)
            iv_bkid = meta.get("iv_bkid")
            fallback = meta.get("fallback_quote_code")
            core_codes = [x["code"] for x in meta.get("core_companies", []) if x.get("code")]
            print(f"\n--- [{theme_id}] {label} ---")

            entry: dict[str, Any] = {"theme_id": theme_id, "label": label}

            # 1. 板块 ret — 优先用主流程已合成的等权指数, 否则 fallback 单 code
            rets: dict[str, Optional[float]] = {"ret_5d": None, "ret_20d": None, "ret_60d": None}
            used_code: Optional[str] = None
            pipeline_entry = pipeline_themes.get(theme_id) if pipeline_themes else None
            proxy_status = (pipeline_entry or {}).get("proxy_status")

            if (pipeline_entry and proxy_status == "composed"
                    and pipeline_entry.get("ret_60d") is not None):
                # 复用主流程已合成的等权指数 (避免雷同 HSTECH 数据)
                rets = {
                    "ret_5d":  pipeline_entry.get("ret_5d"),
                    "ret_20d": pipeline_entry.get("ret_20d"),
                    "ret_60d": pipeline_entry.get("ret_60d"),
                }
                used_code = pipeline_entry.get("ths_code") or f"compose({iv_bkid})"
                entry["composition_n_constituents"] = pipeline_entry.get("composition_n_constituents")
                entry["index_source"] = "pipeline_composed"
                print(f"  板块[composed]: 5d={rets['ret_5d']} 20d={rets['ret_20d']} "
                      f"60d={rets['ret_60d']} ({used_code}, "
                      f"n={entry.get('composition_n_constituents')})")
            else:
                # 主流程未跑 / 合成失败 → fallback 拉单 code
                try:
                    if dry_run:
                        closes, used_code = [100.0]*61, "(dry_run)"
                    else:
                        closes, used_code = fetch_index_close_series(iv_bkid, fallback, today)
                    rets = compute_returns(closes)
                    entry["index_source"] = "fallback_quote"
                    if pipeline_entry:
                        entry["pipeline_proxy_status"] = proxy_status  # 透出主流程降级原因
                    print(f"  板块[fallback]: 5d={rets['ret_5d']} 20d={rets['ret_20d']} "
                          f"60d={rets['ret_60d']} ({used_code})")
                except Exception as e:
                    entry["index_error"] = f"{type(e).__name__}: {e}"
                    print(f"  板块: 失败 {e}")

            entry.update(rets)
            entry["index_used"] = used_code

            # 2. 核心公司 PE_TTM
            try:
                pe_map = {} if dry_run else fetch_pe_ttm(core_codes, today)
                pes = [v for v in pe_map.values() if v is not None]
                pe_avg = sum(pes) / len(pes) if pes else None
                entry["pe_ttm_avg"] = pe_avg
                entry["pe_ttm_per_company"] = pe_map
                print(f"  PE_TTM 平均: {pe_avg} (n={len(pes)}/{len(core_codes)})")
            except Exception as e:
                entry["pe_error"] = f"{type(e).__name__}: {e}"
                pe_avg = None

            # 3. 研报 (7d)
            reports_per_code: dict[str, list[dict]] = {}
            if not dry_run:
                for c in core_codes[:8]:  # 最多 8 只防止过慢
                    reports_per_code[c] = fetch_research_reports(c, today, lookback_days=7)
            research = aggregate_research_sentiment(reports_per_code)
            entry["research"] = research
            print(f"  研报 7d: n={research['n_reports']}, 均评级={research['avg_rating_score']}")

            # 4. 聚合打分 (Kimi 优先, 否则启发式)
            signals = {
                "ret_5d": rets.get("ret_5d"),
                "ret_20d": rets.get("ret_20d"),
                "ret_60d": rets.get("ret_60d"),
                "pe_ttm_avg": pe_avg,
                "research_n_reports": research["n_reports"],
                "research_avg_rating": research["avg_rating_score"],
                "research_n_buy": research["n_buy"],
                "research_n_sell": research["n_sell"],
                "n_core_companies": len(core_codes),
            }
            scored = None
            if use_kimi and not dry_run:
                try:
                    scored = call_kimi_score(theme_id, label, signals)
                    print(f"  热度分 (Kimi): {scored['heat_score']} — {scored['reason']}")
                except Exception as e:
                    print(f"  Kimi 失败, 降级启发式: {e}")
            if scored is None:
                hs = heuristic_heat_score(rets.get("ret_5d"), rets.get("ret_20d"),
                                          rets.get("ret_60d"), pe_avg, research)
                warn = "over_heated" if hs > 80 else ("under_heated" if hs < 40 else None)
                scored = {"heat_score": int(round(hs)), "reason": "heuristic fallback",
                          "warning": warn, "source": "heuristic"}
                print(f"  热度分 (启发式): {scored['heat_score']}")
            entry.update(scored)
            out["themes"][theme_id] = entry
    finally:
        if not dry_run:
            logout_ifind()

    return out


# ============================================================================
# 持久化
# ============================================================================
def append_history(out: dict, dry_run: bool = False) -> None:
    """追加一行到 themes/history.csv. 列 = date + 每个主题一列 (heat_score)."""
    if dry_run:
        return
    asof = out["as_of"]
    themes = out["themes"]
    theme_ids = sorted(themes.keys())
    new_row = {"date": asof}
    for tid in theme_ids:
        new_row[tid] = themes[tid].get("heat_score")

    file_exists = HISTORY_PATH.exists()
    existing_cols: list[str] = []
    existing_rows: list[dict] = []
    if file_exists:
        with HISTORY_PATH.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            existing_cols = list(reader.fieldnames or [])
            existing_rows = [r for r in reader if r.get("date") != asof]  # 同日去重 (重跑覆盖)

    all_cols = ["date"] + sorted(set(existing_cols[1:]) | set(theme_ids))
    with HISTORY_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=all_cols)
        writer.writeheader()
        for r in existing_rows:
            writer.writerow(r)
        writer.writerow(new_row)
    print(f"\n[history] 已写入 {HISTORY_PATH} (主题列: {len(theme_ids)})")


def write_heat_today(out: dict, dry_run: bool = False) -> None:
    """写 themes/heat_today.json (HTML 工具直接 fetch)."""
    if dry_run:
        return
    HEAT_TODAY_PATH.write_text(
        json.dumps(out, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[heat_today] 已写入 {HEAT_TODAY_PATH}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--date", type=str, help="YYYY-MM-DD; 默认今天")
    p.add_argument("--dry-run", action="store_true", help="不调 iFinD/Kimi, 只测路径")
    p.add_argument("--no-kimi", action="store_true", help="跳过 Kimi, 用启发式打分")
    args = p.parse_args()

    today = (datetime.strptime(args.date, "%Y-%m-%d").date()
             if args.date else date.today())

    try:
        out = run(today, dry_run=args.dry_run, use_kimi=not args.no_kimi)
    except Exception as e:
        print(f"FATAL: {type(e).__name__}: {e}")
        import traceback; traceback.print_exc()
        return 2

    append_history(out, dry_run=args.dry_run)
    write_heat_today(out, dry_run=args.dry_run)

    # 简表
    print("\n=== 主题热度榜 ===")
    rows = sorted(out["themes"].values(), key=lambda x: x.get("heat_score", -1), reverse=True)
    for r in rows:
        ws = r.get("warning") or ""
        tag = ("[OVERHEATED]" if ws == "over_heated"
               else "[BOTTOM]" if ws == "under_heated" else "")
        print(f"  {r.get('heat_score'):>4} | {r.get('label'):<20} {tag} {r.get('reason','')[:60]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
