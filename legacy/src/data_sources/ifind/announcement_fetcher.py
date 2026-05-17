"""
announcement_fetcher.py — iFinD 上市公司公告查询 (HTTP report_query)

接口端点:
    POST https://quantapi.51ifind.com/api/v1/report_query

参数 schema (与 SDK 函数 THS_ReportQuery 等价):
    必需:
        codes      证券代码列表 (逗号分隔), 港股形如 '00700.HK', A 股形如 '001339.SZ'
        outputpara 指定返回字段, 形如 'reportDate:Y;thscode:Y;...' (分号分隔, Y/N 标记)
    可选 (顶层直接放, 不嵌套 functionpara):
        beginrDate 起始公告日期 'YYYY-MM-DD'
        endrDate   截止公告日期 'YYYY-MM-DD'
        reportType 公告类型代码 (按 iFinD 字典)
        keyWord    标题关键字 (字段名按文档可能为 keyword/keyWord, 透传给调用方)

返回字段 (按 outputpara 选取):
    thscode      证券代码
    secName      证券简称
    reportDate   公告日期
    ctime        发布时间
    reportTitle  公告标题
    reportType   公告类型
    pdfURL       PDF 下载链接
    seq          序号

返回结构兼容两种:
    A. {"tables": [{"thscode": "...", "table": {col: [v, v, ...]}}]}
    B. {"tables": [{"thscode": "...", "table": [{col: v}, {col: v}]}]}

限制:
    - 公告夜间入库, 当日实时查询可能没有最新数据
    - 单 access_token 最多 20 个 IP
    - PDF 链接可直接 GET 下载 (无需 access_token)
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from .http_client import (
    IFindHttpClient,
    IFindHttpError,
    IFindAuthError,
    get_default_client,
)

REPORT_QUERY_PATH = "/api/v1/report_query"

# 默认请求字段集 (与 SDK THS_ReportQuery 官方示例一致, 全部小写驼峰)
DEFAULT_OUTPUT_FIELDS = [
    "reportDate",
    "thscode",
    "secName",
    "ctime",
    "reportTitle",
    "reportType",
    "pdfURL",
    "seq",
]


# ============================================================================
# 港股代码标准化: report_query 要求 4 位港股代码 (0700.HK), 而项目其它地方常用 5 位 (00700.HK)
# ============================================================================
import re as _re
_HK_FULL_RE = _re.compile(r"^0*(\d{1,5})\.HK$", _re.IGNORECASE)


def _normalize_code_for_report_query(code: str) -> str:
    """
    把港股代码标准化为 report_query 期望格式:
      '00700.HK' / '0700.HK' / '700.HK' → '0700.HK' (4 位补零, 大写后缀)
    A 股 / 美股 / 指数等其它后缀原样返回.
    """
    s = code.strip()
    m = _HK_FULL_RE.match(s)
    if m:
        digits = m.group(1)
        # 港股代码长度 4-5 位实际存在 (如 09618.HK = 京东集团 5 位也合法),
        # 但 report_query 测试得到只接受 4 位; 若数字本身 ≥5 位, 也保留原长.
        if len(digits) <= 4:
            return f"{digits.zfill(4)}.HK"
        return f"{digits}.HK"
    return s


@dataclass
class AnnouncementRecord:
    """单条公告记录 (与项目 dataclass 风格一致, 提供 to_dict)."""

    stock_code: str
    company_name: str
    announcement_date: Optional[str] = None  # ISO 'YYYY-MM-DD'
    publish_datetime: Optional[str] = None   # ISO 'YYYY-MM-DD HH:MM:SS'
    title: str = ""
    report_type: str = ""
    pdf_url: str = ""
    raw_fields: dict[str, Any] = field(default_factory=dict)
    source: str = "ifind_report_query"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # raw_fields 内部保留原始返回, 给下游做扩展用
        return d


# ============================================================================
# 工具: 把 'YYYYMMDD' / 'YYYY-MM-DD' / datetime 统一成 'YYYY-MM-DD' 字符串
# ============================================================================
def _to_ymd(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    s = str(value).strip()
    if not s:
        return None
    # 兼容 'YYYYMMDD'
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s


# ============================================================================
# 主接口
# ============================================================================
def fetch_announcements(
    codes: Iterable[str] | str,
    start_date: date | str | None = None,
    end_date: date | str | None = None,
    keyword: Optional[str] = None,
    report_type: Optional[str] = None,
    output_fields: Optional[list[str]] = None,
    extra_params: Optional[dict[str, Any]] = None,
    client: Optional[IFindHttpClient] = None,
) -> list[AnnouncementRecord]:
    """
    查询上市公司公告.

    参数:
        codes           港股代码 (单个或可迭代列表), 形如 '00700.HK'
        start_date      起始公告日期 (date 或 'YYYY-MM-DD')
        end_date        截止公告日期
        keyword         标题关键字
        report_type     公告类型代码 (按 iFinD 字典, 例如 '901')
        output_fields   返回字段集, 默认 DEFAULT_OUTPUT_FIELDS
        extra_params    顶层透传字段 (覆盖默认值), e.g. {"otherKey": "v"}

    返回:
        list[AnnouncementRecord]

    异常:
        IFindAuthError    refresh_token 缺失或失效
        IFindHttpError    其它接口错误 / 网络错误
    """
    if isinstance(codes, str):
        codes_list = [codes]
    else:
        codes_list = [c for c in codes if c]
    if not codes_list:
        return []

    # 港股代码标准化: report_query 要求 4 位 ('0700.HK'), 其它代码原样
    codes_list = [_normalize_code_for_report_query(c) for c in codes_list]

    output_fields = output_fields or DEFAULT_OUTPUT_FIELDS

    body: dict[str, Any] = {
        "codes": ",".join(codes_list),
        # outputpara: 'field:Y,field2:Y' 逗号分隔, Y/N 标记 (与官方 example 一致)
        "outputpara": ",".join(f"{f}:Y" for f in output_fields),
    }

    sd = _to_ymd(start_date)
    ed = _to_ymd(end_date)
    if sd:
        body["beginrDate"] = sd
    if ed:
        body["endrDate"] = ed

    function_para: dict[str, Any] = {}
    if report_type:
        function_para["reportType"] = str(report_type)
    if keyword:
        function_para["keyWord"] = keyword
    if function_para:
        body["functionpara"] = function_para

    if extra_params:
        body.update(extra_params)

    cli = client or get_default_client()
    payload = cli.post(REPORT_QUERY_PATH, body)
    return _parse_payload(payload, output_fields)


def _parse_payload(
    payload: dict[str, Any],
    output_fields: list[str],
) -> list[AnnouncementRecord]:
    """
    iFinD HTTP 返回常见两种形态:
      A. {"tables": [{"thscode": "...", "table": {col: [v1, v2, ...]}}]}   列存
      B. {"tables": [{"thscode": "...", "table": [{col: v}, {col: v}]}]}  行存
    本函数对两种都兼容.
    """
    records: list[AnnouncementRecord] = []
    tables = payload.get("tables") or payload.get("data") or []
    if not isinstance(tables, list):
        return records

    for tbl in tables:
        if not isinstance(tbl, dict):
            continue
        code = tbl.get("thscode") or tbl.get("thsCode") or ""
        sec_name = tbl.get("secName") or tbl.get("sec_name") or ""
        inner = tbl.get("table")

        if isinstance(inner, dict):
            # 列存: dict[field] = list
            length = 0
            for v in inner.values():
                if isinstance(v, list):
                    length = max(length, len(v))
            for i in range(length):
                row = {k: (v[i] if isinstance(v, list) and i < len(v) else None)
                       for k, v in inner.items()}
                records.append(_row_to_record(code, sec_name, row))
        elif isinstance(inner, list):
            # 行存: list[dict]
            for row in inner:
                if isinstance(row, dict):
                    records.append(_row_to_record(code, sec_name, row))

    return records


def _row_to_record(default_code: str, default_sec_name: str, row: dict[str, Any]) -> AnnouncementRecord:
    def pick(*keys: str) -> Any:
        for k in keys:
            if k in row and row[k] not in (None, ""):
                return row[k]
        return None

    return AnnouncementRecord(
        stock_code=str(pick("thscode", "thsCode") or default_code or ""),
        company_name=str(pick("secName", "sec_name") or default_sec_name or ""),
        announcement_date=_to_ymd(pick("reportDate", "report_date")),
        publish_datetime=str(pick("ctime", "publishDateTime", "publish_datetime") or "") or None,
        title=str(pick("reportTitle", "report_title") or ""),
        report_type=str(pick("reportType", "report_type") or ""),
        pdf_url=str(pick("pdfURL", "pdf_url") or ""),
        raw_fields=dict(row),
    )


# ============================================================================
# 便捷函数: 下载 PDF
# ============================================================================
def download_announcement_pdf(
    record: AnnouncementRecord,
    out_dir: Path,
    timeout: int = 30,
    overwrite: bool = False,
) -> Optional[Path]:
    """
    把 AnnouncementRecord.pdf_url 下载到 out_dir.
    文件名 = '{date}_{stock_code}_{title前30字}.pdf' (清理非法字符).
    """
    if not record.pdf_url:
        return None

    try:
        import requests
    except ImportError as e:
        raise RuntimeError(f"requests 未安装: {e}. pip install requests")

    out_dir.mkdir(parents=True, exist_ok=True)
    safe_title = "".join(
        c if c.isalnum() or c in "-_." else "_" for c in record.title[:30]
    )
    fname = f"{record.announcement_date or 'unknown'}_{record.stock_code}_{safe_title}.pdf"
    out_path = out_dir / fname

    if out_path.exists() and not overwrite:
        return out_path

    resp = requests.get(record.pdf_url, timeout=timeout)
    resp.raise_for_status()
    out_path.write_bytes(resp.content)
    return out_path
