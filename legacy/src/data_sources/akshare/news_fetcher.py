"""
news_fetcher.py — 港股新闻资讯抓取 (akshare)

iFinD 无独立的纯新闻 API (探针确认 -5100 / 空表 / 占位链接), 改用 akshare:
    - ak.stock_news_em(symbol)        东方财富个股新闻 (支持 A/港/美股)
                                       港股 symbol 形如 '00700' (无 .HK 后缀)

输出统一: NewsRecord dataclass

注意:
    - akshare 返回的发布时间格式为 'YYYY-MM-DD HH:MM:SS'
    - 列名中文: '关键词','新闻标题','新闻内容','发布时间','文章来源','新闻链接'
    - 该接口直接走东方财富网, 无需 token, 但有访问频率限制 (经验值: 每秒 ≤2 次)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict, field
from datetime import datetime
from typing import Any, Iterable, Optional

# ============================================================================
# 数据模型
# ============================================================================
@dataclass
class NewsRecord:
    stock_code: str                          # 港股代码, 形如 '00700.HK'
    company_keyword: str                     # akshare 返回的关键词
    headline: str
    published_at: Optional[str] = None       # 'YYYY-MM-DD HH:MM:SS'
    content: str = ""
    source_name: str = ""                    # 媒体名称
    source_url: str = ""
    raw_fields: dict[str, Any] = field(default_factory=dict)
    fetched_via: str = "akshare:stock_news_em"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ============================================================================
# 代码格式转换
# ============================================================================
_HK_CODE_RE = re.compile(r"^(\d{4,5})(?:\.HK)?$", re.IGNORECASE)


def normalize_hk_symbol_for_akshare(code: str) -> str:
    """
    把 '00700.HK' / '700.HK' / '00700' 统一成 akshare 期望的 '00700'.
    akshare 的 stock_news_em 港股个股需要 5 位补零代码.
    """
    if not code:
        return ""
    m = _HK_CODE_RE.match(code.strip())
    if not m:
        return code.strip().upper().replace(".HK", "").lstrip("0").zfill(5)
    digits = m.group(1)
    return digits.zfill(5)


# ============================================================================
# 主接口
# ============================================================================
def fetch_news(
    symbol: str,
    limit: Optional[int] = None,
    start_datetime: Optional[str] = None,
    end_datetime: Optional[str] = None,
) -> list[NewsRecord]:
    """
    拉取单只港股的最近新闻.

    参数:
        symbol           港股代码, 接受 '00700.HK' / '700.HK' / '00700' 等
        limit            返回条数上限
        start_datetime   过滤区间下界 (含), 'YYYY-MM-DD' 或 'YYYY-MM-DD HH:MM:SS'
        end_datetime     过滤区间上界 (含)

    返回:
        list[NewsRecord]

    异常:
        ImportError       akshare 未安装
        RuntimeError      akshare 调用失败
    """
    try:
        import akshare as ak
    except ImportError as e:
        raise ImportError(f"akshare 未安装, 请 pip install akshare. 原始错误: {e}")

    ak_symbol = normalize_hk_symbol_for_akshare(symbol)
    if not ak_symbol:
        return []

    try:
        df = ak.stock_news_em(symbol=ak_symbol)
    except Exception as e:
        raise RuntimeError(f"akshare.stock_news_em({ak_symbol!r}) 失败: {type(e).__name__}: {e}")

    if df is None or len(df) == 0:
        return []

    # 港股代码统一加 .HK 后缀回写到 NewsRecord
    canonical_code = f"{ak_symbol}.HK"

    sd = _parse_dt(start_datetime)
    ed = _parse_dt(end_datetime)

    records: list[NewsRecord] = []
    for _, row in df.iterrows():
        published_str = _safe_str(row.get("发布时间"))
        if (sd or ed) and published_str:
            try:
                pub_dt = datetime.strptime(published_str, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                pub_dt = None
            if pub_dt:
                if sd and pub_dt < sd:
                    continue
                if ed and pub_dt > ed:
                    continue

        records.append(NewsRecord(
            stock_code=canonical_code,
            company_keyword=_safe_str(row.get("关键词")),
            headline=_safe_str(row.get("新闻标题")),
            content=_safe_str(row.get("新闻内容")),
            published_at=published_str or None,
            source_name=_safe_str(row.get("文章来源")),
            source_url=_safe_str(row.get("新闻链接")),
            raw_fields={str(k): _safe_str(v) for k, v in row.items()},
        ))

        if limit and len(records) >= limit:
            break

    return records


def fetch_news_batch(
    symbols: Iterable[str],
    limit_per_symbol: Optional[int] = None,
    start_datetime: Optional[str] = None,
    end_datetime: Optional[str] = None,
    sleep_sec: float = 0.5,
) -> dict[str, list[NewsRecord]]:
    """
    批量拉取多只港股新闻. 单股失败不影响其它, 错误写入 _errors 键.

    返回:
        {
            "00700.HK": [NewsRecord, ...],
            ...,
            "_errors": {"00xxx.HK": "error message", ...}
        }
    """
    import time

    out: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for sym in symbols:
        canonical = f"{normalize_hk_symbol_for_akshare(sym)}.HK"
        try:
            out[canonical] = fetch_news(
                sym,
                limit=limit_per_symbol,
                start_datetime=start_datetime,
                end_datetime=end_datetime,
            )
        except Exception as e:
            out[canonical] = []
            errors[canonical] = f"{type(e).__name__}: {e}"
        if sleep_sec:
            time.sleep(sleep_sec)
    if errors:
        out["_errors"] = errors
    return out


# ============================================================================
# 辅助
# ============================================================================
def _safe_str(v: Any) -> str:
    if v is None:
        return ""
    try:
        import pandas as pd
        if pd.isna(v):
            return ""
    except Exception:
        pass
    return str(v).strip()


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None
