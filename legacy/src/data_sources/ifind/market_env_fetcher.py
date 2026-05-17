"""
market_env_fetcher.py — 给定 asof_date, 返回 MarketEnvironment 8 字段中
                         需要 iFinD 的部分 (HSI block / PE_TTM 百分位 / 南向资金).

设计:
    1. 纯函数, 不写库, 不读 .env (调用方负责登录)
    2. 失败立刻 raise RuntimeError, 由 dao 层统一捕获并 fallback
    3. sector_60d_vol_annualized 退化为 hsi_60d_vol_annualized (本轮不接行业指数)
    4. hk_ipo_30d_* 不在这里; dao 层从 DB 算 (数据本来就在库)

复用:
    - .env 加载逻辑由调用方 (dao.fetch_market_env_at) 触发, 因为 .env 路径
      与本模块目录关联 (src/data_sources/ifind/.env)
    - _hq_unpack: 此处 copy-paste 自 scripts/fetch_hk_market_data.py:120 (避免动那边)
"""
from __future__ import annotations

import math
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional


_ENV_LOADED = False


def _load_env_once() -> None:
    """加载 src/data_sources/ifind/.env (idempotent)"""
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    env_path = Path(__file__).resolve().parent / ".env"
    if env_path.exists():
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    _ENV_LOADED = True


_LOGIN_OK = False


def login_ifind() -> bool:
    """复用 fetch_hk_market_data.py:592-602 的登录逻辑. idempotent."""
    global _LOGIN_OK
    if _LOGIN_OK:
        return True
    _load_env_once()
    user = os.environ.get("IFIND_USERNAME", "")
    pwd = os.environ.get("IFIND_PASSWORD", "")
    if not user or not pwd:
        raise RuntimeError("未读到 IFIND_USERNAME / IFIND_PASSWORD (.env 缺失或未配置)")
    from iFinDPy import THS_iFinDLogin
    code = THS_iFinDLogin(user, pwd)
    if code not in (0, -201):
        raise RuntimeError(f"iFinD 登录失败: code={code}")
    _LOGIN_OK = True
    return True


# ---- 解包工具 (copy-paste from fetch_hk_market_data.py:120, 本轮先不抽公共) ----
def _hq_unpack(result: Any, debug_tag: str = "") -> "object":
    class _Out:
        errorcode = -1
        errmsg = "unknown"
        closes: list[float] = []
        opens: list[float] = []
        times: list[Any] = []
        values: list[float] = []
        raw = None

    out = _Out()
    out.raw = result

    # 形态 B: 对象
    if hasattr(result, "errorcode"):
        out.errorcode = int(result.errorcode)
        out.errmsg = str(getattr(result, "errmsg", ""))
        df = getattr(result, "data", None)
        if df is not None and hasattr(df, "columns"):
            if "close" in df.columns:
                out.closes = [float(x) for x in df["close"].dropna().tolist()]
            if "open" in df.columns:
                out.opens = [float(x) for x in df["open"].dropna().tolist()]
            time_col = next((c for c in df.columns
                             if c.lower() in ("time", "date", "thsdate")), None)
            if time_col:
                out.times = df[time_col].tolist()
            # 任意 indicator 列 (如 ths_pe_ttm_index) 落到 values
            indicator_cols = [c for c in df.columns
                              if c not in ("close", "open", "time", "date", "thsdate")]
            if indicator_cols:
                col = indicator_cols[0]
                out.values = [float(x) for x in df[col].dropna().tolist()]
        return out

    # 形态 A: OrderedDict
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
                if "open" in tbl:
                    out.opens = [float(x) for x in tbl["open"] if x is not None]
                # 任意非 close/open 的 key 当作 indicator
                for k, v in tbl.items():
                    if k not in ("close", "open") and isinstance(v, list) and v:
                        out.values = [float(x) for x in v if x is not None]
                        break
            if isinstance(t0, dict) and "time" in t0:
                out.times = list(t0["time"])
        return out

    out.errmsg = f"unknown return type: {type(result).__name__}"
    return out


# ---- 字段计算函数 ----
def fetch_hsi_block(end_date: date) -> dict:
    """
    调用 1 次 THS_HistoryQuotes('HSI.HK', end-1y, end, ['close']),
    返回 {hsi_60d_return, hsi_60d_vol_annualized, hsi_60d_vol_pct_rank}.
    需要 1 年序列才能算 60d 波动率百分位.
    """
    from iFinDPy import THS_HistoryQuotes

    sdate = (end_date - timedelta(days=400)).strftime("%Y-%m-%d")
    edate = end_date.strftime("%Y-%m-%d")

    r = THS_HistoryQuotes("HSI.HK", "close", "", sdate, edate)
    u = _hq_unpack(r, debug_tag="env:HSI.HK")
    if u.errorcode != 0:
        raise RuntimeError(f"HSI.HK ec={u.errorcode} msg={u.errmsg}")
    closes = u.closes
    if len(closes) < 65:
        raise RuntimeError(f"HSI.HK closes 不足 ({len(closes)} < 65)")

    closes_60 = closes[-60:]
    hsi_60d_return = closes_60[-1] / closes_60[0] - 1

    rets_60 = [closes_60[i] / closes_60[i - 1] - 1 for i in range(1, len(closes_60))]
    mean = sum(rets_60) / len(rets_60)
    var = sum((x - mean) ** 2 for x in rets_60) / (len(rets_60) - 1)
    vol_60 = math.sqrt(var) * math.sqrt(252)

    # 滚动 60d 波动率
    rets_full = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
    rolling_vols = []
    for end in range(60, len(rets_full) + 1):
        window = rets_full[end - 60:end]
        m = sum(window) / 60
        v = sum((x - m) ** 2 for x in window) / 59
        rolling_vols.append(math.sqrt(v) * math.sqrt(252))
    ref = rolling_vols[-252:] if len(rolling_vols) > 252 else rolling_vols
    if not ref:
        raise RuntimeError("HSI.HK rolling_vols 为空")
    rank = sum(1 for v in ref if v <= vol_60) / len(ref)

    return {
        "hsi_60d_return": float(hsi_60d_return),
        "hsi_60d_vol_annualized": float(vol_60),
        "hsi_60d_vol_pct_rank": float(rank),
    }


def fetch_pe_ttm_pct(end_date: date) -> Optional[float]:
    """
    HSI 当前 PE_TTM 在过去 5 年序列里的百分位 [0,1].
    iFinD 字段名 ths_pe_ttm_index. 失败返回 None (不 raise, 让上游决定).
    """
    from iFinDPy import THS_HistoryQuotes

    sdate = (end_date - timedelta(days=365 * 5 + 30)).strftime("%Y-%m-%d")
    edate = end_date.strftime("%Y-%m-%d")
    try:
        r = THS_HistoryQuotes("HSI.HK", "ths_pe_ttm_index", "", sdate, edate)
        u = _hq_unpack(r, debug_tag="env:HSI.PE_TTM")
        if u.errorcode != 0:
            return None
        series = u.values or u.closes  # 兜底: 不同版本可能落到 closes
        series = [v for v in series if v and v > 0]
        if len(series) < 100:
            return None
        latest = series[-1]
        rank = sum(1 for v in series if v <= latest) / len(series)
        return float(rank)
    except Exception:
        return None


def fetch_southbound_normalized(end_date: date) -> Optional[float]:
    """
    THS_EDB('S032219215') 过去 12 个月跨境理财通南向, 用 5%/95% 分位线
    标准化最近月份到 [-1, 1]. 失败返回 None.
    """
    from iFinDPy import THS_EDB

    sdate = (end_date - timedelta(days=400)).strftime("%Y-%m-%d")
    edate = end_date.strftime("%Y-%m-%d")
    try:
        r = THS_EDB("S032219215", "", sdate, edate)
        if getattr(r, "errorcode", -1) != 0:
            return None
        df = getattr(r, "data", None)
        if df is None or len(df) == 0:
            return None
        # data 列是 'value'
        df_sorted = df.sort_values("time").reset_index(drop=True)
        values = []
        for _, row in df_sorted.iterrows():
            v = row.get("value")
            try:
                if v is not None and str(v).lower() not in ("nan", "none"):
                    values.append(float(v))
            except (ValueError, TypeError):
                continue
        if len(values) < 6:
            return None
        latest = values[-1]
        # 5%/95% 分位线标准化
        sv = sorted(values)
        n = len(sv)
        p5 = sv[max(0, int(n * 0.05))]
        p95 = sv[min(n - 1, int(n * 0.95))]
        if p95 <= p5:
            return 0.0
        midpoint = (p5 + p95) / 2.0
        half_range = (p95 - p5) / 2.0
        normalized = (latest - midpoint) / half_range
        # clip to [-1, 1]
        return float(max(-1.0, min(1.0, normalized)))
    except Exception:
        return None


def fetch_market_env_dict(end_date: date) -> dict:
    """
    主入口: 给定 end_date, 调 iFinD 拼装出 MarketEnvironment 6 个字段
    (不含 hk_ipo_30d_* 那 2 个; 由 DAO 层从 DB 算).

    返回 dict, 包含 keys:
      hsi_60d_return, hsi_60d_vol_annualized, hsi_60d_vol_pct_rank,
      hsi_valuation_pct, southbound_30d_net_normalized,
      sector_60d_vol_annualized
    任一关键字段失败即 raise RuntimeError, 调用方走 fallback.
    """
    login_ifind()

    hsi = fetch_hsi_block(end_date)        # 必需
    pe_pct = fetch_pe_ttm_pct(end_date)    # 关键, 失败即抛
    if pe_pct is None:
        raise RuntimeError("PE_TTM 百分位获取失败")
    sb_norm = fetch_southbound_normalized(end_date)  # 失败可填 0
    if sb_norm is None:
        sb_norm = 0.0

    return {
        "hsi_60d_return": hsi["hsi_60d_return"],
        "hsi_60d_vol_annualized": hsi["hsi_60d_vol_annualized"],
        "hsi_60d_vol_pct_rank": hsi["hsi_60d_vol_pct_rank"],
        "hsi_valuation_pct": pe_pct,
        "southbound_30d_net_normalized": sb_norm,
        # sector 退化为 hsi vol (本轮决策, 见计划)
        "sector_60d_vol_annualized": hsi["hsi_60d_vol_annualized"],
    }
