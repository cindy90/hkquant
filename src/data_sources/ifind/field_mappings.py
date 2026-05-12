"""
iFinD raw CSV → SQLite schema 字段映射

把 iFinD 报表字段 ID (p05309_f001, p05310_f001 等无语义代号) 映射到
人类可读的语义列名, 供 load_to_db.py 消费.

映射来源:
    1. full_data_pull.py 的 THS_DR 调用顺序 (字段 ID 排列)
    2. 实际 CSV vs nacs_real.db 已有数据反推 (e.g. 1187.HK 泰德医药)
    3. iFinD 客户端「数据浏览器」字段说明 (人工核对 ~50%)

⚠ 字段确认状态:
    - p05309: 18 字段中 17 字段已确认; f019 确认为 investor_background (53% 有值)
    - p05310: 54 字段中核心 ~20 字段已确认, 剩余低覆盖字段暂不映射
    - sponsor_tier / gics_l2 等字段从 iFinD 其他接口或人工标注获取, 不在本 CSV 中
"""
from __future__ import annotations

# =============================================================================
# p05309 基石投资者 (18 字段)
# =============================================================================
# 一行 = 一只 IPO × 一个基石; 同一 IPO 多基石 = 多行
P05309_CORNERSTONES = {
    "p05309_f001": "stock_code",          # 港股代码 (1187.HK)
    "p05309_f002": "company_name_zh",     # 公司中文名
    "p05309_f003": "listing_date",        # 上市日期 (yyyy/mm/dd)
    "p05309_f016": "ipo_announce_date",   # 招股公告日
    "p05309_f004": "pricing_date",        # 定价日
    "p05309_f017": "is_offshore_yn",      # 是 / 否 (含义不完全确认)
    "p05309_f005": "cornerstone_name",    # ★ 基石原文名 (招股书署名)
    "p05309_f018": "cornerstone_desc",    # 基石简介文本
    "p05309_f006": "ultimate_holder",     # ★ 实际控制人 (用于 cluster 识别)
    "p05309_f019": "investor_background",  # 基石投资者背景/关联描述 (53% 有值, 较长文本)
    "p05309_f009": "allocation_shares",   # ★ 认购股数
    "p05309_f008": "ticket_size_hkd",     # ★ 认购金额 HKD
    "p05309_f011": "currency",            # 币种 (HKD / USD / CNY)
    "p05309_f014": "subscribe_pct",       # 占发行比例 %
    "p05309_f010": "lockup_months",       # ★ 锁定期月数
    "p05309_f015": "unlock_date",         # 解禁日期
    "p05309_f012": "hangseng_industry",   # 恒生一级行业
    "p05309_f013": "hangseng_subindustry",  # 恒生二级行业
}

# =============================================================================
# p05310 首发信息一览 (54 字段)
# =============================================================================
# 注: 大量字段对当前模型无用; 此处只映射 ETL 需要的 ~15 个核心字段.
# 未列入的字段会被 loader 忽略 (不报错).
P05310_IPO_INFO = {
    "p05310_f001": "stock_code",            # 港股代码
    "p05310_f002": "company_name_zh",       # 公司名
    "p05310_f008": "offer_price_high",      # 招股价上限
    "p05310_f009": "offer_price_low",       # 招股价下限 (P1 补齐, 用于 pricing_in_range)
    "p05310_f010": "offer_price_hkd",       # ★ 实际定价 (招股价)
    "p05310_f013": "total_offer_shares",    # 全球发售股数
    "p05310_f015": "public_offer_shares",   # 公开发售股数 (回拨前)
    "p05310_f017": "intl_offer_shares",     # 国际配售股数
    "p05310_f023": "offering_size_hkd",     # ★ 募集总额 HKD
    "p05310_f025": "offering_size_net_hkd", # 募集净额
    "p05310_f027": "public_oversub",        # ★ 公开超额认购倍数
    # ★ P0-#2 修复 (经数据样本反推 f028~f034 实际含义):
    #     f028 = 招股截止日 = 真实定价日 (基石协议在此日前签署, 防 look-ahead 切点)
    #     f029 = 配售结果公布日
    #     f032 = 暗盘日 (上市前 1 天)        ← 旧版误标为 pricing_date, 已弃用
    #     f033 = 正式上市日                  ← listing_date 维持不变
    "p05310_f028": "pricing_date",          # ★ 定价日 (招股截止日)
    "p05310_f033": "listing_date",          # ★ 上市日
    "p05310_f039": "currency",              # 币种
    "p05310_f049": "use_of_proceeds",       # 募资用途文本
    "p05310_f050": "cornerstone_coverage",  # ★ 基石覆盖率 % (注: 是%数, 非小数)
    "p05310_f052": "intl_oversub",          # ★ 国际配售超额认购倍数
}

# =============================================================================
# THS_BD 财务 (年报)
# =============================================================================
# 这张表字段名已经语义化, 直接列名即可
FINANCIALS_ANNUAL = {
    "thscode": "stock_code",
    "total_oi": "revenue",                   # 总营业收入 (本币)
    "gross_selling_rate": "gross_margin",    # 销售毛利率 %
    "net_profit_margin_on_sales": "net_margin",  # 销售净利率 %
    "ths_roe_hks": "roe",                    # ROE %
    "ni_attr_to_cs": "net_profit_attr",      # 归母净利润
    "report_year": "report_year",
}

# =============================================================================
# THS_BD 股本指标
# =============================================================================
SHARE_CAPITAL = {
    "thscode": "stock_code",
    "post_ipo_shares": "post_ipo_shares",        # 首发后总股本
    "actual_issued_shares": "actual_issued_shares",  # 实际发行股本(含超配)
    "pre_ipo_shares": "pre_ipo_shares",          # 推算: 上市前总股本
}

# =============================================================================
# 退市/收购 (反幸存者偏差)
# =============================================================================
# delisted_pull.py 输出的 CSV 列已是语义名, 这里只做识别
DELISTED_HK = {
    "stock_code": "stock_code",            # 港股代码
    "delisting_date": "delisting_date",    # ISO date
    "delisting_reason": "delisting_reason",  # acquired / liquidated / suspended_long / regulatory
    "is_acquired": "is_acquired",          # 0/1
}


# =============================================================================
# THS_DataPool 板块成分 (18A/18C/AH/SPAC)
# =============================================================================
# 单文件 ifind_blocks.csv 含多个 block_name 的 union
BLOCKS = {
    "thscode": "stock_code",
    "security_name": "company_name_zh",
    "block_name": "block_name",  # '18A' / '18C' / 'AH' / '18B_SPAC'
}

# block_name → ListingChapter 枚举值
BLOCK_TO_CHAPTER = {
    "18A": "18a",
    "18C": "18c_commercial",        # 默认按商业化档; 未商业化档 (precommercial) 需人工区分
    "AH": "a_plus_h",
    "18B_SPAC": "spac",
}


# =============================================================================
# Helper: 类型转换 + 缺失值统一
# =============================================================================

NULL_TOKENS = {"", "--", "—", "NULL", "NaN", "nan", "null", "None"}


def parse_float(v) -> float | None:
    """iFinD 用 '--' 表示缺失, 转 None"""
    if v is None:
        return None
    s = str(v).strip()
    if s in NULL_TOKENS:
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def parse_int(v) -> int | None:
    f = parse_float(v)
    if f is None:
        return None
    return round(f)


def parse_date(v) -> str | None:
    """yyyy/mm/dd or yyyy-mm-dd → ISO date string"""
    from datetime import date as _date

    if v is None:
        return None
    s = str(v).strip()
    if s in NULL_TOKENS:
        return None
    s = s.replace("/", "-").replace(".", "-")
    parts = s.split("-")
    if len(parts) != 3:
        return None
    try:
        y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
        _date(y, m, d)  # 严格校验: 闰日、月天数等均由 stdlib 保证
        return f"{y:04d}-{m:02d}-{d:02d}"
    except (ValueError, TypeError, OverflowError):
        return None


def parse_str(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    if s in NULL_TOKENS:
        return None
    return s


def make_ipo_id(stock_code: str, listing_date_iso: str) -> str:
    """与现有 DB 一致的 ipo_id 格式: HK_{code}_{year}, 点号转下划线

    e.g. (1187.HK, 2026-05-06) -> HK_1187_HK_2026
    """
    code = stock_code.replace(".", "_")
    year = listing_date_iso[:4]
    return f"HK_{code}_{year}"


def make_cornerstone_id(canonical_name: str) -> str:
    """与现有 DB 兼容的 cornerstone_id 格式: CS_<name>

    实际 DB 同时存在两种风格:
        - CS_<UPPERCASED_ASCII>   (如 CS_JPMAMAPL) — 老英文基石
        - CS_<原中文>             (如 CS_蓝思科技_香港_有限公司) — 新中文基石
    保持这种风格: 含中文则保留中文, 全英文则上格式化, 括号转下划线.
    """
    name = canonical_name.strip()
    # 简化: 把括号、空格、点号、引号等转下划线; 保留中英文/数字
    bad = "()（）.,。， '\"-/\\"
    for ch in bad:
        name = name.replace(ch, "_")
    # 折叠多个下划线
    while "__" in name:
        name = name.replace("__", "_")
    name = name.strip("_")
    return f"CS_{name}"


# =============================================================================
# FX 汇率: 按季度查表 (替代硬编码常数)
# =============================================================================
# 数据来源: 港元联系汇率 + 离岸人民币季末中间价
# USD/HKD 受联系汇率制约 (7.75-7.85), 变动极小;
# CNY/HKD = USD/HKD ÷ USD/CNY, 波动区间 ~1.07-1.23.
# EUR/GBP/JPY: 港股基石偶尔出现的小币种, 季末近似值 (央行公布中间价).
#
# 表格格式: dict[currency] -> list[(季度起始日, rate_to_HKD)]
# 查找逻辑: 取 ≤ asof_date 的最近一条; 超出表格范围则用最近值外推.

_FX_QUARTERLY_USD: list[tuple[str, float]] = [
    ("2022-01-01", 7.80), ("2022-04-01", 7.83), ("2022-07-01", 7.83), ("2022-10-01", 7.83),
    ("2023-01-01", 7.83), ("2023-04-01", 7.83), ("2023-07-01", 7.82), ("2023-10-01", 7.82),
    ("2024-01-01", 7.82), ("2024-04-01", 7.81), ("2024-07-01", 7.80), ("2024-10-01", 7.80),
    ("2025-01-01", 7.79), ("2025-04-01", 7.79), ("2025-07-01", 7.79), ("2025-10-01", 7.79),
    ("2026-01-01", 7.78), ("2026-04-01", 7.78),
]

_FX_QUARTERLY_CNY: list[tuple[str, float]] = [
    ("2022-01-01", 1.23), ("2022-04-01", 1.17), ("2022-07-01", 1.13), ("2022-10-01", 1.09),
    ("2023-01-01", 1.14), ("2023-04-01", 1.08), ("2023-07-01", 1.08), ("2023-10-01", 1.10),
    ("2024-01-01", 1.08), ("2024-04-01", 1.08), ("2024-07-01", 1.11), ("2024-10-01", 1.07),
    ("2025-01-01", 1.07), ("2025-04-01", 1.08), ("2025-07-01", 1.08), ("2025-10-01", 1.07),
    ("2026-01-01", 1.07), ("2026-04-01", 1.07),
]

# EUR→HKD ≈ EUR/USD × USD/HKD; 季末近似
_FX_QUARTERLY_EUR: list[tuple[str, float]] = [
    ("2022-01-01", 8.83), ("2022-04-01", 8.25), ("2022-07-01", 7.80), ("2022-10-01", 8.03),
    ("2023-01-01", 8.40), ("2023-04-01", 8.58), ("2023-07-01", 8.50), ("2023-10-01", 8.28),
    ("2024-01-01", 8.62), ("2024-04-01", 8.35), ("2024-07-01", 8.47), ("2024-10-01", 8.45),
    ("2025-01-01", 8.42), ("2025-04-01", 8.50), ("2025-07-01", 8.50), ("2025-10-01", 8.50),
    ("2026-01-01", 8.48), ("2026-04-01", 8.48),
]

# GBP→HKD ≈ GBP/USD × USD/HKD; 季末近似
_FX_QUARTERLY_GBP: list[tuple[str, float]] = [
    ("2022-01-01", 10.55), ("2022-04-01", 9.67), ("2022-07-01", 9.33), ("2022-10-01", 9.47),
    ("2023-01-01", 9.64), ("2023-04-01", 9.82), ("2023-07-01", 9.91), ("2023-10-01", 9.79),
    ("2024-01-01", 9.96), ("2024-04-01", 9.86), ("2024-07-01", 10.05), ("2024-10-01", 10.05),
    ("2025-01-01", 9.90), ("2025-04-01", 10.00), ("2025-07-01", 10.00), ("2025-10-01", 10.00),
    ("2026-01-01", 9.95), ("2026-04-01", 9.95),
]

# JPY→HKD: 日元兑港元 (1 JPY → HKD); 因日元基数小, 值约 0.05~0.07
_FX_QUARTERLY_JPY: list[tuple[str, float]] = [
    ("2022-01-01", 0.0678), ("2022-04-01", 0.0607), ("2022-07-01", 0.0570), ("2022-10-01", 0.0540),
    ("2023-01-01", 0.0597), ("2023-04-01", 0.0588), ("2023-07-01", 0.0536), ("2023-10-01", 0.0524),
    ("2024-01-01", 0.0551), ("2024-04-01", 0.0500), ("2024-07-01", 0.0500), ("2024-10-01", 0.0510),
    ("2025-01-01", 0.0507), ("2025-04-01", 0.0520), ("2025-07-01", 0.0520), ("2025-10-01", 0.0520),
    ("2026-01-01", 0.0518), ("2026-04-01", 0.0518),
]

# 所有支持币种的季度表映射
_FX_TABLES: dict[str, list[tuple[str, float]]] = {
    "USD": _FX_QUARTERLY_USD,
    "CNY": _FX_QUARTERLY_CNY,
    "EUR": _FX_QUARTERLY_EUR,
    "GBP": _FX_QUARTERLY_GBP,
    "JPY": _FX_QUARTERLY_JPY,
}

# 所有支持的外币
SUPPORTED_CURRENCIES = frozenset(("HKD", "USD", "CNY", "EUR", "GBP", "JPY"))

# 向后兼容: 无日期时的默认值 (与旧常数一致)
FX_USD_HKD_DEFAULT = 7.80
FX_CNY_HKD_DEFAULT = 1.10
_FX_DEFAULTS: dict[str, float] = {
    "USD": 7.80,
    "CNY": 1.10,
    "EUR": 8.48,
    "GBP": 9.95,
    "JPY": 0.0518,
}


def _lookup_quarterly(table: list[tuple[str, float]], asof_date: str) -> float:
    """在季度表中查找 ≤ asof_date 的最近条目."""
    best = table[0]
    for entry in table:
        if entry[0] <= asof_date:
            best = entry
        else:
            break
    return best[1]


def get_fx_rate(currency: str, asof_date: str | None = None) -> float:
    """返回指定币种在 asof_date 附近的 → HKD 汇率.

    Args:
        currency: 'USD' / 'CNY' / 'HKD' / 'EUR' / 'GBP' / 'JPY' (不区分大小写)
        asof_date: ISO date (yyyy-mm-dd); None 时回退到默认常数

    Returns:
        1 unit currency = ? HKD

    Raises:
        ValueError: 不支持的币种
    """
    c = (currency or "HKD").upper()
    if c == "HKD":
        return 1.0

    if c not in _FX_TABLES:
        raise ValueError(
            f"不支持的币种 {c!r}; 支持列表: {sorted(SUPPORTED_CURRENCIES)}"
        )

    if asof_date is None:
        return _FX_DEFAULTS[c]

    return _lookup_quarterly(_FX_TABLES[c], asof_date)
