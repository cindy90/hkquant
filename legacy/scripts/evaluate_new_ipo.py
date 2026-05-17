"""
evaluate_new_ipo.py — 拟上市公司一键评估: iFinD 拉取 → YAML → 入库 → NACS → 报告

端到端流程:
    1. iFinD 查询 p05310 (首发信息) + p05309 (基石投资者) + THS_BD (三年财务)
    2. 自动生成 data/deals/<stock_code>.yaml
    3. load_deal 写入 ipo_master + ipo_cornerstone_link
    4. analyze_deal --persist 跑 NACS 评分并落盘 nacs_predictions
    5. 生成 outputs/<stock_code>_analysis_<date>.md 分析报告

用法:
    # 基本用法
    python scripts/evaluate_new_ipo.py --stock-code 6871.HK

    # 指定上市章节 (18c_commercial / main_board_profitable / 18a / ...)
    python scripts/evaluate_new_ipo.py --stock-code 6871.HK --chapter 18c_commercial

    # 指定公司类型 (tech_18c / profitable / biotech_18a)
    python scripts/evaluate_new_ipo.py --stock-code 6871.HK --company-type tech_18c

    # 跳过 iFinD 拉取 (YAML 已存在时直接评估)
    python scripts/evaluate_new_ipo.py --stock-code 6871.HK --skip-ifind

    # dry-run: 只拉数据和生成 YAML, 不入库不评估
    python scripts/evaluate_new_ipo.py --stock-code 6871.HK --dry-run

依赖:
    - iFinDPy (iFinD 终端已登录)
    - pyyaml
    - 项目 src/ 和 scripts/ 的现有模块
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 让 src/ 和 scripts/ 在 sys.path 中
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "scripts"))

# Windows 控制台 UTF-8
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from log import get_logger, setup_cli_logging

setup_cli_logging("INFO")
_log = get_logger("evaluate_new_ipo")


# =============================================================================
# 1. iFinD 数据拉取
# =============================================================================

def _login_ifind() -> None:
    """加载 .env 并登录 iFinD"""
    env_path = _ROOT / "src" / "data_sources" / "ifind" / ".env"
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

    from iFinDPy import THS_iFinDLogin
    user = os.environ.get("IFIND_USERNAME", "")
    pwd = os.environ.get("IFIND_PASSWORD", "")
    if not user or not pwd:
        raise SystemExit("未读到 IFIND_USERNAME / IFIND_PASSWORD, 检查 .env")
    code = THS_iFinDLogin(user, pwd)
    if code not in (0, -201):
        raise SystemExit(f"iFinD 登录失败: code={code}")
    _log.info("iFinD 登录成功")


@dataclass
class IFinDData:
    """iFinD 拉取结果的结构化容器"""
    stock_code: str
    company_name_zh: str
    # p05310 首发信息
    offer_price_hkd: Optional[float] = None
    offer_price_low: Optional[float] = None
    offer_price_high: Optional[float] = None
    total_offer_shares: Optional[int] = None
    public_offer_shares: Optional[int] = None
    intl_offer_shares: Optional[int] = None
    offering_size_hkd: Optional[float] = None
    greenshoe_shares: int = 0
    cornerstone_amount: float = 0.0
    cornerstone_coverage: float = 0.0
    listing_date: Optional[str] = None
    pricing_date: Optional[str] = None
    sponsor: Optional[str] = None
    currency: str = "HKD"
    use_of_proceeds: Optional[str] = None
    public_oversub: Optional[float] = None
    intl_oversub: Optional[float] = None
    # p05309 基石投资者
    cornerstones: List[Dict] = None
    # THS_BD 财务
    financials: List[Dict] = None
    # 推算
    post_ipo_shares: Optional[int] = None
    pre_ipo_shares: Optional[int] = None
    mkt_cap_hkd: Optional[float] = None

    def __post_init__(self):
        if self.cornerstones is None:
            self.cornerstones = []
        if self.financials is None:
            self.financials = []


def _parse_val(v) -> Optional[float]:
    """iFinD 用 '--' / None 表示缺失"""
    if v is None:
        return None
    s = str(v).strip()
    if s in ("", "--", "None", "NaN", "nan", "null"):
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _parse_int(v) -> Optional[int]:
    f = _parse_val(v)
    return round(f) if f is not None else None


def _parse_str(v) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    if s in ("", "--", "None"):
        return None
    return s


def fetch_ipo_info(stock_code: str) -> Optional[Dict]:
    """从 p05310 拉取指定股票的首发信息"""
    from iFinDPy import THS_DR

    # 用较宽的日期范围搜索
    today = date.today()
    sdate = f"{today.year - 1}0101"
    edate = f"{today.year}1231"

    all_fields = ",".join([f"p05310_f{i:03d}:Y" for i in range(1, 55)])
    result = THS_DR(
        "p05310",
        f"ttype=1;sdate={sdate};edate={edate};sfzx=1",
        all_fields,
        "format:dataframe",
    )
    if result.errorcode != 0 or result.data is None:
        _log.warning("p05310 查询失败: ec=%s, %s", result.errorcode, result.errmsg)
        return None

    df = result.data
    # 在 f001 (stock_code) 列中查找
    code_num = stock_code.replace(".HK", "")
    mask = df["p05310_f001"].astype(str).str.contains(code_num, na=False)
    if not mask.any():
        _log.warning("p05310 中未找到 %s", stock_code)
        return None

    row = df[mask].iloc[0]
    return {f"f{i:03d}": row.get(f"p05310_f{i:03d}") for i in range(1, 55)}


def fetch_cornerstones(stock_code: str) -> List[Dict]:
    """从 p05309 拉取基石投资者"""
    from iFinDPy import THS_DR
    from data_sources.ifind.field_mappings import P05309_CORNERSTONES

    today = date.today()
    sdate = f"{today.year - 1}0101"
    edate = f"{today.year}1231"

    fields = ",".join([f"{k}:Y" for k in P05309_CORNERSTONES.keys()])
    result = THS_DR(
        "p05309",
        f"ttype=1;sdate={sdate};edate={edate};sfzx=1",
        fields,
        "format:dataframe",
    )
    if result.errorcode != 0 or result.data is None:
        _log.warning("p05309 查询失败: ec=%s", result.errorcode)
        return []

    df = result.data
    code_num = stock_code.replace(".HK", "")
    # 搜索 stock_code 列
    code_col = "p05309_f001"
    mask = df[code_col].astype(str).str.contains(code_num, na=False)
    if not mask.any():
        _log.info("p05309 中无 %s 的基石记录 — 确认无基石投资者", stock_code)
        return []

    rows = df[mask]
    cs_list = []
    for _, r in rows.iterrows():
        cs = {}
        for raw_key, semantic_key in P05309_CORNERSTONES.items():
            cs[semantic_key] = r.get(raw_key)
        cs_list.append(cs)

    _log.info("p05309: %s 有 %d 个基石投资者", stock_code, len(cs_list))
    return cs_list


def fetch_financials(stock_code: str) -> List[Dict]:
    """从 THS_BD 拉取三年财务数据"""
    from iFinDPy import THS_BD

    indicators = (
        "total_oi;"
        "gross_selling_rate;"
        "net_profit_margin_on_sales;"
        "ths_roe_hks;"
        "ni_attr_to_cs"
    )
    results = []
    for year in range(date.today().year - 3, date.today().year + 1):
        params = (
            f"{year}-12-31;"
            f"{year}-12-31,104;"
            f"{year}-12-31,104;"
            f"{year}-12-31,100;"
            f"{year}-12-31,100,OC"
        )
        r = THS_BD(stock_code, indicators, params)
        if r.errorcode == 0 and r.data is not None:
            rec = r.data.to_dict("records")[0]
            rec["report_year"] = year
            results.append(rec)
        time.sleep(0.3)

    _log.info("THS_BD: %s 拉到 %d 年财务数据", stock_code, len(results))
    return results


def pull_all_ifind(stock_code: str) -> IFinDData:
    """一站式拉取 iFinD 全部数据"""
    _login_ifind()

    ipo_info = fetch_ipo_info(stock_code)
    cs_list = fetch_cornerstones(stock_code)
    fin_list = fetch_financials(stock_code)

    data = IFinDData(
        stock_code=stock_code,
        company_name_zh="",
        cornerstones=cs_list,
        financials=fin_list,
    )

    if ipo_info:
        data.company_name_zh = _parse_str(ipo_info.get("f002")) or stock_code
        data.offer_price_hkd = _parse_val(ipo_info.get("f010"))
        data.offer_price_low = _parse_val(ipo_info.get("f009"))
        data.offer_price_high = _parse_val(ipo_info.get("f008"))
        data.total_offer_shares = _parse_int(ipo_info.get("f013"))
        data.public_offer_shares = _parse_int(ipo_info.get("f015"))
        data.intl_offer_shares = _parse_int(ipo_info.get("f017"))
        data.greenshoe_shares = _parse_int(ipo_info.get("f041")) or 0
        data.cornerstone_amount = _parse_val(ipo_info.get("f047")) or 0.0
        data.cornerstone_coverage = _parse_val(ipo_info.get("f048")) or 0.0
        data.listing_date = _parse_str(ipo_info.get("f033"))
        data.pricing_date = _parse_str(ipo_info.get("f028"))
        data.sponsor = _parse_str(ipo_info.get("f004"))
        data.currency = _parse_str(ipo_info.get("f039")) or "HKD"
        data.use_of_proceeds = _parse_str(ipo_info.get("f049"))
        data.public_oversub = _parse_val(ipo_info.get("f027"))
        data.intl_oversub = _parse_val(ipo_info.get("f052"))

        # 推算
        price = data.offer_price_hkd or 0
        shares = data.total_offer_shares or 0
        data.offering_size_hkd = price * shares if price and shares else None

        # 尝试推算总市值和股本
        if data.offering_size_hkd and data.cornerstone_coverage:
            pass  # 有基石覆盖率时可以反推, 但这里简化
    else:
        _log.warning("p05310 无数据, YAML 将缺少发行信息")
        data.company_name_zh = stock_code

    return data


# =============================================================================
# 2. YAML 生成
# =============================================================================

def _infer_chapter(data: IFinDData, override: Optional[str]) -> str:
    if override:
        return override
    # 默认 main_board_profitable
    return "main_board_profitable"


def _infer_company_type(chapter: str, override: Optional[str]) -> str:
    if override:
        return override
    mapping = {
        "18c_commercial": "tech_18c",
        "18c_precommercial": "tech_18c",
        "18a": "biotech_18a",
    }
    return mapping.get(chapter, "profitable")


def _format_date(raw: Optional[str]) -> Optional[str]:
    """yyyy/mm/dd → yyyy-mm-dd"""
    if not raw:
        return None
    return raw.replace("/", "-")[:10]


def generate_deal_yaml(data: IFinDData, chapter: str, company_type: str,
                       analyst_notes: str = "") -> str:
    """生成 deal YAML 文本"""
    import yaml

    listing_date = _format_date(data.listing_date) or "2026-01-01"

    # 推算股本
    price = data.offer_price_hkd or 0
    greenshoe_pct = 0.0
    if data.total_offer_shares and data.total_offer_shares > 0:
        greenshoe_pct = round(data.greenshoe_shares / data.total_offer_shares, 4)

    # 保荐人 tier: 简单规则
    sponsor_name = data.sponsor or ""
    tier1_keywords = ["中金", "高盛", "摩根士丹利", "摩根大通", "瑞银", "华泰国际",
                      "CICC", "Goldman", "Morgan Stanley", "JPMorgan", "UBS"]
    sponsor_tier = 2
    for kw in tier1_keywords:
        if kw in sponsor_name:
            sponsor_tier = 1
            break
    # 取第一个保荐人名
    primary_sponsor = sponsor_name.split(",")[0].strip() if sponsor_name else None

    # 基石投资者
    cs_yaml = []
    for cs in data.cornerstones:
        cs_entry = {
            "cornerstone_name": cs.get("cornerstone_name") or "Unknown",
            "cornerstone_type": "family_office_spv",  # 默认, 需人工修正
            "ticket_size_native": _parse_val(cs.get("ticket_size_hkd")) or 0,
            "currency": cs.get("currency") or "HKD",
            "lockup_months": _parse_int(cs.get("lockup_months")) or 6,
            "ultimate_holder": cs.get("ultimate_holder"),
            "affiliation_flag": 0,
        }
        cs_yaml.append(cs_entry)

    # 财务摘要用于 analyst_notes
    fin_notes = []
    for f in sorted(data.financials, key=lambda x: x.get("report_year", 0)):
        yr = f.get("report_year", "?")
        rev = f.get("total_oi")
        gm = f.get("gross_selling_rate")
        nm = f.get("net_profit_margin_on_sales")
        roe = f.get("ths_roe_hks")
        ni = f.get("ni_attr_to_cs")
        parts = [f"{yr}年:"]
        if rev: parts.append(f"收入{rev / 1e8:.2f}亿")
        if gm: parts.append(f"毛利率{gm:.1f}%")
        if nm: parts.append(f"净利率{nm:.1f}%")
        if roe: parts.append(f"ROE{roe:.1f}%")
        if ni: parts.append(f"归母净利{ni / 1e8:.2f}亿")
        fin_notes.append(" ".join(parts))

    auto_notes = (
        f"iFinD 自动拉取 ({date.today().isoformat()})\n"
        f"保荐人: {sponsor_name}\n"
        f"基石数: {len(cs_yaml)}, 覆盖率: {data.cornerstone_coverage}%\n"
        f"公开超购: {data.public_oversub or '--'}倍\n"
    )
    if fin_notes:
        auto_notes += "财务摘要 (iFinD THS_BD):\n  " + "\n  ".join(fin_notes) + "\n"
    if analyst_notes:
        auto_notes += f"\n人工备注:\n{analyst_notes}\n"

    deal = {
        "stock_code": data.stock_code,
        "company_name_zh": data.company_name_zh,
        "listing_chapter": chapter,
        "company_type": company_type,
        "expected_listing_date": listing_date,
        "prospectus_pdf_path": None,
        "analyst_notes": auto_notes,
        "ipo_master_overrides": {
            "offer_price_low": data.offer_price_low,
            "offer_price_high": data.offer_price_high,
            "total_offer_shares": data.total_offer_shares,
            "offering_size_hkd": data.offering_size_hkd,
            "greenshoe_pct": greenshoe_pct,
            "lockup_months": 6,
            "sponsor_primary": primary_sponsor,
            "sponsor_tier": sponsor_tier,
            "joint_sponsor_count": max(1, sponsor_name.count(",") + 1)
                                  if sponsor_name else 1,
            "auditor_tier": 2,
            "pe_at_offer": None,
            "pe_peer_median": None,
            "gics_l2": None,
            "pre_ipo_shares": data.pre_ipo_shares,
            "post_ipo_shares": data.post_ipo_shares,
            "overhang_ratio": (round(data.pre_ipo_shares / data.total_offer_shares, 1)
                               if data.pre_ipo_shares and data.total_offer_shares
                               else None),
        },
        "cornerstones": cs_yaml if cs_yaml else [],
        "themes": {
            "ai_revenue_pct": None,
            "ai_revenue_source": None,
            "override_theme_id": None,
        },
    }
    return yaml.dump(deal, allow_unicode=True, default_flow_style=False,
                     sort_keys=False, width=120)


# =============================================================================
# 3. 报告生成
# =============================================================================

def generate_report(stock_code: str, data: IFinDData, nacs_output: str,
                    chapter: str) -> str:
    """生成 Markdown 分析报告"""
    today_str = date.today().isoformat()

    # 财务表
    fin_rows = []
    for f in sorted(data.financials, key=lambda x: x.get("report_year", 0)):
        yr = f.get("report_year", "?")
        rev = f.get("total_oi")
        gm = f.get("gross_selling_rate")
        nm = f.get("net_profit_margin_on_sales")
        roe = f.get("ths_roe_hks")
        ni = f.get("ni_attr_to_cs")
        fin_rows.append(
            f"| {yr} | {rev / 1e8:.2f} | {gm:.1f}% | {nm:.1f}% | "
            f"{roe:.1f}% | {ni / 1e8:.2f} |"
            if all(x is not None for x in [rev, gm, nm, roe, ni])
            else f"| {yr} | -- | -- | -- | -- | -- |"
        )

    report = f"""# {data.company_name_zh} ({stock_code}) 基石投资机会评估报告

> 分析日期: {today_str} | 预期上市: {_format_date(data.listing_date) or '--'} | NACS v8 框架
> 数据来源: iFinD (THS_DR p05310/p05309, THS_BD), 项目数据库
> Deal YAML: data/deals/{stock_code}.yaml

---

## 一、公司概况

| 项目 | 详情 |
|------|------|
| 股票代码 | {stock_code} |
| 公司名称 | {data.company_name_zh} |
| 上市章节 | {chapter} |
| 预期上市 | {_format_date(data.listing_date) or '--'} |
| 发行价 | {data.offer_price_hkd or '--'} HKD |
| 募资额 | {data.offering_size_hkd / 1e8:.2f} 亿港元 |
| 保荐人 | {data.sponsor or '--'} |
| 基石投资者 | {len(data.cornerstones)} 个 (覆盖率 {data.cornerstone_coverage}%) |
| 绿鞋 | {data.greenshoe_shares} 股 |

---

## 二、三年财务数据 (iFinD THS_BD)

| 年份 | 收入 (亿元) | 毛利率 | 净利率 | ROE | 归母净利 (亿元) |
|------|-----------|--------|--------|------|--------------|
{chr(10).join(fin_rows)}

---

## 三、NACS v8 模型评分 (analyze_deal.py 输出)

```
{nacs_output}
```

---

## 四、后续 Review 检查清单

上市后按以下时间点 review:

- [ ] **D1**: 首日涨跌幅
- [ ] **D30**: 30 日回报
- [ ] **M3**: 中期表现
- [ ] **M6**: 解禁窗口
- [ ] **M12**: 长期表现

### Review 命令

```bash
# 更新收益数据
python scripts/fix_p1_returns_via_ifind.py

# 重新评估
python scripts/analyze_deal.py --stock-code {stock_code}

# case review
python scripts/case_review.py
```

---

*报告生成: evaluate_new_ipo.py | {today_str}*
"""
    return report


# =============================================================================
# 4. 主流程
# =============================================================================

def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--stock-code", required=True, help="港股代码 (如 6871.HK)")
    ap.add_argument("--chapter", help="上市章节 (如 18c_commercial, main_board_profitable)")
    ap.add_argument("--company-type", help="公司类型 (如 tech_18c, profitable, biotech_18a)")
    ap.add_argument("--analyst-notes", default="", help="人工备注")
    ap.add_argument("--db", default=str(_ROOT / "data" / "nacs_real.db"))
    ap.add_argument("--skip-ifind", action="store_true",
                    help="跳过 iFinD 拉取 (YAML 已存在时)")
    ap.add_argument("--skip-nacs", action="store_true",
                    help="跳过 NACS 评估 (只拉数据和生成 YAML)")
    ap.add_argument("--dry-run", action="store_true",
                    help="只拉数据和生成 YAML, 不入库不评估")
    ap.add_argument("--force", action="store_true",
                    help="强制覆盖已存在的 YAML")
    args = ap.parse_args()

    stock_code = args.stock_code.upper()
    if not stock_code.endswith(".HK"):
        stock_code += ".HK"

    yaml_path = _ROOT / "data" / "deals" / f"{stock_code}.yaml"
    today_str = date.today().strftime("%Y%m%d")
    report_path = _ROOT / "outputs" / f"{stock_code}_analysis_{today_str}.md"

    # ── Step 1: iFinD 拉取 ──
    data: Optional[IFinDData] = None
    if not args.skip_ifind:
        _log.info("=" * 60)
        _log.info("Step 1: iFinD 数据拉取 — %s", stock_code)
        _log.info("=" * 60)
        data = pull_all_ifind(stock_code)
        _log.info("  公司: %s", data.company_name_zh)
        _log.info("  发行价: %s HKD", data.offer_price_hkd)
        _log.info("  基石: %d 个, 覆盖率 %s%%", len(data.cornerstones),
                  data.cornerstone_coverage)
        _log.info("  财务: %d 年数据", len(data.financials))
    else:
        _log.info("跳过 iFinD 拉取 (--skip-ifind)")

    # ── Step 2: 生成 YAML ──
    if data and (not yaml_path.exists() or args.force):
        _log.info("=" * 60)
        _log.info("Step 2: 生成 Deal YAML → %s", yaml_path)
        _log.info("=" * 60)
        chapter = _infer_chapter(data, args.chapter)
        company_type = _infer_company_type(chapter, args.company_type)
        yaml_text = generate_deal_yaml(data, chapter, company_type,
                                       args.analyst_notes)
        yaml_path.parent.mkdir(parents=True, exist_ok=True)
        yaml_path.write_text(yaml_text, encoding="utf-8")
        _log.info("  YAML 已保存: %s", yaml_path)
    elif yaml_path.exists():
        _log.info("YAML 已存在: %s (用 --force 覆盖)", yaml_path)
    else:
        _log.warning("无 iFinD 数据且 YAML 不存在, 后续步骤将失败")

    if args.dry_run:
        _log.info("=" * 60)
        _log.info("[DRY-RUN] 完成. 文件: %s", yaml_path)
        return 0

    # ── Step 3: 入库 ──
    _log.info("=" * 60)
    _log.info("Step 3: load_deal → ipo_master")
    _log.info("=" * 60)
    if yaml_path.exists():
        from data.deal_loader import load_deal_file, lint_deal
        from data.dao import db_connect
        import yaml as _yaml

        deal_data = _yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        errs = lint_deal(deal_data)
        if errs:
            for e in errs:
                _log.error("  lint: %s", e)
            return 1

        with db_connect(args.db) as conn:
            result = load_deal_file(conn, yaml_path)
            _log.info("  %s: %s", yaml_path.name, result)
    else:
        _log.error("YAML 不存在: %s", yaml_path)
        return 1

    # ── Step 4: NACS 评估 ──
    nacs_output = ""
    if not args.skip_nacs:
        _log.info("=" * 60)
        _log.info("Step 4: analyze_deal --persist")
        _log.info("=" * 60)
        import subprocess
        cmd = [
            sys.executable, "-X", "utf8",
            str(_ROOT / "scripts" / "analyze_deal.py"),
            "--stock-code", stock_code,
            "--persist",
            "--db", args.db,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", cwd=str(_ROOT))
        nacs_output = proc.stdout + proc.stderr
        _log.info(nacs_output)
        if proc.returncode != 0:
            _log.warning("analyze_deal 返回码: %d", proc.returncode)

    # ── Step 5: 生成报告 ──
    _log.info("=" * 60)
    _log.info("Step 5: 生成分析报告 → %s", report_path)
    _log.info("=" * 60)
    if data:
        chapter = args.chapter or _infer_chapter(data, args.chapter)
        report_text = generate_report(stock_code, data, nacs_output, chapter)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report_text, encoding="utf-8")
        _log.info("  报告已保存: %s", report_path)
    else:
        _log.info("  无 iFinD 数据, 跳过报告生成 (可手工补充)")

    # ── 完成 ──
    _log.info("=" * 60)
    _log.info("全部完成!")
    _log.info("  Deal YAML : %s", yaml_path)
    _log.info("  分析报告  : %s", report_path)
    _log.info("  数据库    : %s", args.db)
    _log.info("")
    _log.info("后续 review:")
    _log.info("  python scripts/fix_p1_returns_via_ifind.py   # 拉收益数据")
    _log.info("  python scripts/analyze_deal.py --stock-code %s  # 重新评估",
              stock_code)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
