"""
raw/overrides.yaml 加载与应用

设计:
    raw CSV 是只读的"原始档", 任何人工修正集中在 data/raw/overrides.yaml.
    ETL 在 read_csv_dict() 之后、写库之前调用 apply_ipo_overrides() 把覆盖项
    合并进逐行 dict, 确保 (raw + overrides) → DB 是确定性可重建过程.

YAML 结构:
    ipo_info:
      <stock_code>:
        <semantic_field>: <value>
        _reason: <短描述>
        _source: <来源>

下划线开头字段是元数据 (不写入 DB); 其它字段必须是 P05310_IPO_INFO 语义列名.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional


_PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OVERRIDES_PATH = _PROJECT_ROOT / "data" / "raw" / "overrides.yaml"

# 允许覆盖的 ipo_info 语义列 (与 field_mappings.P05310_IPO_INFO 的 value 集合一致)
ALLOWED_IPO_FIELDS = {
    "stock_code", "company_name_zh",
    "listing_date", "pricing_date",
    "offer_price_hkd", "offer_price_high", "offer_price_low",
    "offering_size_hkd", "offering_size_net_hkd",
    "intl_oversub", "public_oversub",
    "cornerstone_coverage", "total_offer_shares",
    "public_offer_shares", "intl_offer_shares",
    "currency", "use_of_proceeds",
    "listing_chapter",
}


def load_overrides(path: Optional[Path] = None) -> Dict[str, Any]:
    """读取 YAML; 文件不存在或 yaml 不可用时返回空 dict (ETL 退化为无覆盖)."""
    p = path or DEFAULT_OVERRIDES_PATH
    if not p.exists():
        return {}
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        return {}
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def apply_ipo_overrides(rows: List[Dict[str, str]],
                        overrides: Dict[str, Any]) -> List[Dict[str, str]]:
    """把 ipo_info 覆盖合并进逐行 dict.

    返回新列表 (不就地修改); 元数据字段 (_reason / _source) 不写入行.
    未在 ALLOWED_IPO_FIELDS 中的字段会被忽略并打印警告.
    """
    ipo_overrides = (overrides or {}).get("ipo_info", {}) or {}
    if not ipo_overrides:
        return rows

    out: List[Dict[str, str]] = []
    matched_codes: set = set()
    for row in rows:
        code = row.get("stock_code") or ""
        if code and code in ipo_overrides:
            patch = {k: v for k, v in ipo_overrides[code].items()
                     if not k.startswith("_") and k in ALLOWED_IPO_FIELDS}
            merged = dict(row)
            merged.update({k: str(v) if v is not None else "" for k, v in patch.items()})
            out.append(merged)
            matched_codes.add(code)
        else:
            out.append(row)

    # 检查覆盖文件中有, 但 raw CSV 里没有的 stock_code (可能是数据重 pull 后股票被删)
    csv_codes = {r.get("stock_code") for r in rows if r.get("stock_code")}
    orphans = set(ipo_overrides.keys()) - csv_codes
    if orphans:
        import sys
        sys.stderr.write(
            f"[overrides] WARN: {len(orphans)} stock_codes in overrides 不在 raw CSV: "
            f"{sorted(orphans)[:5]}{'...' if len(orphans) > 5 else ''}\n"
        )

    return out


def lint_overrides(overrides: Dict[str, Any]) -> List[str]:
    """返回错误列表; 空表示通过 (用于 CI / pre-commit).

    检查项:
      - ipo_info 顶层是 dict
      - 每个 stock_code 至少有一个真实字段 (非 _meta)
      - 每个真实字段都在 ALLOWED_IPO_FIELDS
      - _reason / _source 必填 (强制留痕)
    """
    errs: List[str] = []
    ipo = (overrides or {}).get("ipo_info", {}) or {}
    if not isinstance(ipo, dict):
        return [f"ipo_info 顶层必须是 dict, 得到 {type(ipo).__name__}"]
    for code, patch in ipo.items():
        if not isinstance(patch, dict):
            errs.append(f"{code}: 必须是 dict")
            continue
        real = {k: v for k, v in patch.items() if not k.startswith("_")}
        if not real:
            errs.append(f"{code}: 没有任何修正字段")
        bad = [k for k in real if k not in ALLOWED_IPO_FIELDS]
        if bad:
            errs.append(f"{code}: 不允许覆盖的字段 {bad}")
        if not patch.get("_reason"):
            errs.append(f"{code}: 缺少 _reason")
        if not patch.get("_source"):
            errs.append(f"{code}: 缺少 _source")
    return errs
