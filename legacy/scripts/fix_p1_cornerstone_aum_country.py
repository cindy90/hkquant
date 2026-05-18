"""
P1-#7 修复: 填充 cornerstone_master 的 country_of_origin / aum_usd_latest.

背景:
    1311 行 cornerstone_master 中, 4 个字段 100% NULL:
        parent_entity, country_of_origin, aum_usd_latest, aum_asof_date
    其中 aum_usd 进 score_individual_cornerstone 公式 (log scale 0-15 分),
    NULL → 0 分, 大牌基石被低估. country 仅 metadata.

策略 (低成本, 不破坏现状):
    1. AUM: 维护 ~50 条手工字典 (公开年报数据), 按名称关键词匹配, 单位 USD;
       不命中保留 NULL.
    2. country: 启发式规则:
       - cornerstone_type='cn_mutual_insurance' or 'policy_fund' → CN
       - is_chinese=1 → CN
       - 名称含 "Singapore" / "Pte" → SG
       - 名称含 "(Hong Kong)" / "Hong Kong" / "HK)" → HK
       - 名称含 "(Cayman)" / "SPC" / "Limited" 但全英文 → INTL (兜底)
       - 全英文且无明确地理标记 → US (大头, 因 BlackRock/JPM/GS 等都美国)
       不命中保留 NULL.

数据来源 (AUM 估值, USD billion, 最新公开数据 ≈ 2024-2025 年报):
    BlackRock 11500B, Vanguard 9300B (未现身港股), JPMAM 3500B, GS AM 2900B, Invesco 1700B,
    Fidelity 5400B, Schroder 1100B, M&G 470B, AXA IM 850B, Allianz GI 600B, Abrdn 600B,
    Baillie Gifford 270B, GIC 770B, Temasek 290B, Mubadala 280B, Oaktree 200B,
    高瓴/HHLR 130B, 富达 (Fidelity 中国) 60B, 易方达 (E Fund) 560B, 嘉实 (Harvest) 140B,
    华夏基金 250B, 工银瑞信 (ICBCCS) 250B, 南方基金 (China Southern) 220B,
    中国人寿 670B, 平安 1000B, 太保 200B, 新华保险 145B, 人保资产 150B,
    UBS AM 5500B, Mirae Asset 200B, RBC GAM 580B, Eastspring 200B, Morgan Stanley IM 1500B

用法:
    python scripts/fix_p1_cornerstone_aum_country.py --dry-run
    python scripts/fix_p1_cornerstone_aum_country.py
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "nacs_real.db"

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# (substring, aum_usd, country) — 大小写不敏感, name 命中即填
# AUM 单位: USD (即 1e9 = 1 billion)
# 数据为公开估值, 仅供量化模型 log scale 评分; 不构成投资建议.
AUM_DICT = [
    # === 全球资管巨头 (Global Long-Only / Asset Manager) ===
    ("BlackRock", 11_500e9, "US"),
    ("Vanguard", 9_300e9, "US"),
    ("J.P. Morgan", 3_500e9, "US"),
    ("JPMORGAN", 3_500e9, "US"),
    ("JPMAM", 3_500e9, "US"),
    ("Goldman Sachs", 2_900e9, "US"),
    ("Morgan Stanley", 1_500e9, "US"),
    ("Fidelity", 5_400e9, "US"),
    ("Invesco", 1_700e9, "US"),
    ("Schroder", 1_100e9, "UK"),
    ("Schroders", 1_100e9, "UK"),
    ("AXA Investment", 850e9, "FR"),
    ("AXA INVESTMENT", 850e9, "FR"),
    ("Allianz Global", 600e9, "DE"),
    ("ALLIANZ", 600e9, "DE"),
    ("ABRDN", 600e9, "UK"),
    ("Aberdeen", 600e9, "UK"),
    ("M&G Investment", 470e9, "UK"),
    ("M&G INVESTMENT", 470e9, "UK"),
    ("Baillie Gifford", 270e9, "UK"),
    ("BAILLIE GIFFORD", 270e9, "UK"),
    ("UBS Asset Management", 1_700e9, "CH"),
    ("UBS ASSET MANAGEMENT", 1_700e9, "CH"),
    ("RBC Global Asset", 580e9, "CA"),
    ("RBC GLOBAL ASSET", 580e9, "CA"),
    ("Eastspring", 200e9, "SG"),
    ("EASTSPRING", 200e9, "SG"),
    ("Mirae Asset", 200e9, "KR"),
    ("MIRAE ASSET", 200e9, "KR"),
    ("Pictet", 700e9, "CH"),
    ("PICTET", 700e9, "CH"),

    # === 主权基金 / 退休金 ===
    ("GIC PRIVATE", 770e9, "SG"),
    ("GIC Private", 770e9, "SG"),
    ("TEMASEK", 290e9, "SG"),
    ("Temasek", 290e9, "SG"),
    ("Mubadala", 280e9, "AE"),
    ("MUBADALA", 280e9, "AE"),
    ("Abu Dhabi", 800e9, "AE"),  # ADIA (主权基金) 估算
    ("ADIA", 800e9, "AE"),
    ("Norges Bank", 1_700e9, "NO"),  # 挪威 GPFG
    ("NORGES BANK", 1_700e9, "NO"),
    ("Qatar Investment", 510e9, "QA"),  # QIA
    ("QIA", 510e9, "QA"),

    # === 顶级对冲/PE ===
    ("Oaktree", 200e9, "US"),
    ("OAKTREE", 200e9, "US"),
    ("HHLR", 130e9, "CN"),  # 高瓴 (Hillhouse 香港旗下)
    ("Hillhouse", 130e9, "CN"),
    ("HILLHOUSE", 130e9, "CN"),
    ("Capital Group", 2_700e9, "US"),
    ("CAPITAL GROUP", 2_700e9, "US"),
    ("Wellington", 1_300e9, "US"),
    ("WELLINGTON", 1_300e9, "US"),
    ("T. Rowe Price", 1_650e9, "US"),
    ("T. ROWE PRICE", 1_650e9, "US"),
    ("Lazard", 250e9, "US"),
    ("LAZARD", 250e9, "US"),
    ("Aspex", 5e9, "HK"),
    ("ASPEX", 5e9, "HK"),
    ("Jane Street", 14e9, "US"),  # 估值非 AUM, 但用于 score
    ("JANE STREET", 14e9, "US"),
    ("Jump Trading", 10e9, "US"),
    ("JUMP TRADING", 10e9, "US"),

    # === 中国保险 / 公募 (中文名 + 拼音) ===
    ("中国人寿", 670e9, "CN"),
    ("中国人保", 150e9, "CN"),
    ("人保资产", 150e9, "CN"),
    ("中国平安", 1_000e9, "CN"),
    ("平安资产", 1_000e9, "CN"),
    ("中国太保", 200e9, "CN"),
    ("太保资产", 200e9, "CN"),
    ("太平洋保险", 200e9, "CN"),
    ("新华保险", 145e9, "CN"),
    ("泰康", 200e9, "CN"),
    ("阳光保险", 70e9, "CN"),
    ("华夏保险", 80e9, "CN"),
    ("华泰保兴", 80e9, "CN"),
    ("华泰证券", 80e9, "CN"),
    # 公募 (USD 估)
    ("易方达", 560e9, "CN"),
    ("E Fund", 560e9, "CN"),
    ("E FUND", 560e9, "CN"),
    ("华夏基金", 250e9, "CN"),
    ("China Asset Management", 250e9, "CN"),
    ("嘉实", 140e9, "CN"),
    ("Harvest", 140e9, "CN"),
    ("HARVEST", 140e9, "CN"),
    ("工银瑞信", 250e9, "CN"),
    ("ICBCCS", 250e9, "CN"),
    ("南方基金", 220e9, "CN"),
    ("China Southern", 220e9, "CN"),
    ("广发基金", 200e9, "CN"),
    ("汇添富", 130e9, "CN"),
    ("博时基金", 100e9, "CN"),
    ("招商基金", 130e9, "CN"),
    ("国泰", 100e9, "CN"),
    ("富国基金", 130e9, "CN"),
    ("鹏华", 130e9, "CN"),
]


def lookup_aum(name: str):
    """返回 (aum_usd, country) 命中第一个; 否则 (None, None)"""
    if not name:
        return None, None
    name_lo = name.lower()
    for sub, aum, country in AUM_DICT:
        if sub.lower() in name_lo:
            return aum, country
    return None, None


def derive_country(name: str, ctype: str, is_chinese: int):
    """启发式: 推 country 兜底 (当 AUM 字典未命中时)"""
    if not name:
        return None
    nlo = name.lower()
    # 优先名称地理标记
    if "(singapore)" in nlo or "pte. ltd" in nlo or "pte ltd" in nlo:
        return "SG"
    if "(hong kong)" in nlo or "hong kong" in nlo or "(hk)" in nlo:
        return "HK"
    if "(cayman)" in nlo or "cayman" in nlo:
        return "KY"
    if "(taiwan)" in nlo or "taipei" in nlo:
        return "TW"
    if "(korea)" in nlo or "seoul" in nlo:
        return "KR"
    if "(japan)" in nlo or "tokyo" in nlo:
        return "JP"
    # type 推断
    if ctype in ("cn_mutual_insurance", "policy_fund"):
        return "CN"
    if is_chinese == 1:
        return "CN"
    # 全英文兜底: 大概率国际机构 (但不强制)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not DB.exists():
        print(f"DB 不存在: {DB}")
        return 2

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    rows = cur.execute("""
        SELECT cornerstone_id, canonical_name, cornerstone_type, is_chinese
        FROM cornerstone_master
    """).fetchall()
    print(f"[init] cornerstone_master 共 {len(rows)} 行")

    # 本次 update 不覆盖已有非 NULL 值 (idempotent)
    aum_today = date.today().isoformat()

    aum_hits = 0
    country_hits_dict = 0
    country_hits_heur = 0
    updates = []
    sample = []
    for r in rows:
        name = r["canonical_name"]
        aum, country_from_dict = lookup_aum(name)
        country = country_from_dict
        if country is None:
            country = derive_country(name, r["cornerstone_type"], r["is_chinese"])
            if country is not None:
                country_hits_heur += 1
        else:
            country_hits_dict += 1

        if aum is not None:
            aum_hits += 1

        if aum is not None or country is not None:
            updates.append((aum, aum_today if aum else None, country, r["cornerstone_id"]))
            if len(sample) < 10 and aum is not None:
                sample.append((name, aum, country))

    print(f"[step1] AUM 命中:        {aum_hits} 行")
    print(f"[step2] country (字典):  {country_hits_dict} 行")
    print(f"[step3] country (启发):  {country_hits_heur} 行")
    print(f"[step4] 待 UPDATE:       {len(updates)} 行 (其中 {len(rows)-len(updates)} 行保持 NULL)")
    print()
    print("--- AUM 命中样本 (前 10) ---")
    for name, aum, country in sample:
        print(f"  {name[:60]:60s}  AUM={aum/1e9:6.0f}B  country={country}")

    # country 分布预览
    from collections import Counter
    cdist = Counter(u[2] for u in updates if u[2] is not None)
    print(f"\n--- country 分布 ---")
    for c, n in cdist.most_common():
        print(f"  {c}: {n}")

    if args.dry_run:
        print("\n[dry-run] 不写库")
        conn.close()
        return 0

    # 写入: 仅当现值 NULL 时才覆盖 (用 COALESCE)
    cur.executemany("""
        UPDATE cornerstone_master
        SET aum_usd_latest = COALESCE(aum_usd_latest, ?),
            aum_asof_date = COALESCE(aum_asof_date, ?),
            country_of_origin = COALESCE(country_of_origin, ?),
            updated_at = CURRENT_TIMESTAMP
        WHERE cornerstone_id = ?
    """, updates)
    print(f"\n✅ UPDATE: {cur.rowcount} 行")
    conn.commit()

    # 验证
    print("\n--- 验证 ---")
    n_aum = cur.execute(
        "SELECT COUNT(*) FROM cornerstone_master WHERE aum_usd_latest IS NOT NULL"
    ).fetchone()[0]
    n_country = cur.execute(
        "SELECT COUNT(*) FROM cornerstone_master WHERE country_of_origin IS NOT NULL"
    ).fetchone()[0]
    print(f"  aum_usd_latest 非 NULL:    {n_aum}/{len(rows)}")
    print(f"  country_of_origin 非 NULL: {n_country}/{len(rows)}")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
