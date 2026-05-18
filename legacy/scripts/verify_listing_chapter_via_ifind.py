"""验证 ipo_master.listing_chapter 与 iFinD 板块分类一致性 (P3-#4 验证).

数据源:
    THS_DataPool('block', 'YYYY-MM-DD;{block_id}', 'date:Y,thscode:Y,security_name:Y')

板块 ID (用 p03321 板块ID查询接口验证, 港股上市标准/港股市场类):
    011009001 = 生物科技公司(18A)        - 港股主板特别章节 18A
    011009003 = 特专科技公司(18C)        - 港股主板特别章节 18C
    011001003001 = AH股                  - A股已上市的港股
    011001021002 = 第二上市              - 二次上市 (Chapter 19C)
    011001025002 = De-SPAC上市           - SPAC 并购完成
    011001007 = 港股主板                 - 全部主板 (含 18A/18C)

匹配优先级 (一只股票可能属于多个板块):
    1. 18A (011009001)
    2. 18C (011009003)
    3. secondary (011001021002)
    4. a_plus_h (011001003001)
    5. de-SPAC (011001025002)
    6. main_board (011001007)

输出:
    outputs/verify_chapter_report.csv     全样本对照
    outputs/verify_chapter_mismatch.csv   仅错分清单
    控制台 summary

只读 (不动 DB).

用法:
    python scripts/verify_listing_chapter_via_ifind.py
    python scripts/verify_listing_chapter_via_ifind.py --date 2026-05-09
"""
from __future__ import annotations

import argparse
import csv
import os
import sqlite3
import sys
import time
from collections import defaultdict, OrderedDict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "nacs_real.db"
OUT_DIR = ROOT / "outputs"

sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 板块 ID → (内部 tag, 优先级) — 优先级数字越小越优先
BLOCK_DEFS = [
    ("18a",          "011009001",    1),
    ("18c",          "011009003",    2),
    ("secondary",    "011001021002", 3),
    ("a_plus_h",     "011001003001", 4),
    ("de_spac",      "011001025002", 5),
    ("main_board",   "011001007",    6),
]


def _norm(code: str) -> str:
    """归一化股票代码: '03296.HK' / '3296.HK' → '3296.HK'.

    注: iFinD 板块成分中带 H 前缀的代码 (如 H2530.HK) 是**未上市占位代码**,
    与 ipo_master 中实际上市股票 (2530.HK) 不是同一标的, 必须区别对待.
    本函数返回 None 表示该代码非实际上市状态, 不参与匹配.
    """
    if not isinstance(code, str):
        return None
    c = code.strip()
    # 排除 H 前缀 (未上市占位)
    if c.startswith("H") and len(c) > 1 and c[1].isdigit():
        return None
    h, _, t = c.partition(".")
    h = h.lstrip("0") or "0"
    return h + ("." + t if t else "")


def relogin():
    from src.data_sources.ifind import market_env_fetcher as mef
    mef._LOGIN_OK = False
    mef.login_ifind()


def fetch_block(block_id: str, query_date: str) -> list[tuple[str, str]]:
    """拉取板块成分股, 返回 [(thscode_normalized, name), ...]."""
    from iFinDPy import THS_DataPool
    result = THS_DataPool(
        'block',
        f'{query_date};{block_id}',
        'date:Y,thscode:Y,security_name:Y'
    )
    if not isinstance(result, (dict, OrderedDict)):
        raise RuntimeError(f"DataPool 返回类型异常: {type(result)}")
    if result.get('errorcode') != 0:
        raise RuntimeError(f"DataPool ec={result.get('errorcode')} msg={result.get('errmsg')}")
    tables = result.get('tables')
    if not tables or not isinstance(tables, list):
        return []
    t0 = tables[0]
    if not isinstance(t0, dict) or 'table' not in t0:
        return []
    table = t0['table']
    if not isinstance(table, dict):
        return []
    codes = table.get('THSCODE') or []
    names = table.get('SECURITY_NAME') or []
    out = []
    for sc, nm in zip(codes, names):
        sc_norm = _norm(str(sc).strip())
        if sc_norm is None:  # 跳过 H 前缀的未上市占位
            continue
        out.append((sc_norm, str(nm).strip()))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=date.today().isoformat(),
                    help="查询日期 YYYY-MM-DD (默认今天)")
    args = ap.parse_args()
    query_date = args.date

    if not DB.exists():
        print(f"DB 不存在: {DB}")
        return 2
    OUT_DIR.mkdir(exist_ok=True)

    # 1) 登录 iFinD
    print(f"[step1] 登录 iFinD...")
    relogin()
    print(f"  ✓ 登录 OK")

    # 2) 拉取 6 个板块成分
    print(f"\n[step2] 拉取板块成分 (查询日期 {query_date})")
    block_members = {}  # tag → set(stock_code_normalized)
    for tag, bid, _ in BLOCK_DEFS:
        try:
            t0 = time.time()
            members = fetch_block(bid, query_date)
            block_members[tag] = {sc for sc, _ in members}
            print(f"  ✓ {tag:<12s} (id={bid}): {len(members)} 只 ({time.time()-t0:.1f}s)")
        except Exception as e:
            print(f"  ✗ {tag} (id={bid}) 失败: {e}; 重登重试")
            try:
                relogin()
                members = fetch_block(bid, query_date)
                block_members[tag] = {sc for sc, _ in members}
                print(f"    ✓ 重试成功: {len(members)} 只")
            except Exception as e2:
                print(f"    ✗ 重试仍失败: {e2}")
                block_members[tag] = set()
        time.sleep(0.5)

    # 3) 读 ipo_master
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    rows = cur.execute("""
        SELECT ipo_id, stock_code, company_name_zh, listing_chapter, listing_date
        FROM ipo_master ORDER BY listing_date
    """).fetchall()
    conn.close()
    print(f"\n[step3] ipo_master 共 {len(rows)} 行")

    # 4) 反向匹配 + 输出
    def infer_chapter(sc_norm: str) -> tuple[str, list[str]]:
        """返回 (推断章节标签, 命中板块列表). 按 BLOCK_DEFS 优先级."""
        hits = []
        for tag, _, _ in BLOCK_DEFS:
            if sc_norm in block_members.get(tag, set()):
                hits.append(tag)
        if not hits:
            return ("none", [])
        # 取优先级最高 (即 BLOCK_DEFS 最前)
        for tag, _, _ in BLOCK_DEFS:
            if tag in hits:
                return (tag, hits)
        return ("none", hits)

    # ipo_master.listing_chapter → 期望的 inferred_tag (用于一致性判断)
    EXPECTED_MAP = {
        "18a":                  ["18a"],
        "18c_commercial":       ["18c"],
        "18c_precommercial":    ["18c"],
        "secondary":            ["secondary"],
        "a_plus_h":             ["a_plus_h"],
        "spac":                 ["de_spac"],
        # 主板已盈利 / 未盈利: 应在主板, 但不应在 18A/18C/secondary/spac
        "main_board_profitable":   ["main_board"],
        "main_board_unprofitable": ["main_board"],
    }

    report = []
    n_ok = n_mismatch = n_unknown = 0
    by_status = defaultdict(int)
    for ipo_id, sc, name, db_chap, ld in rows:
        sc_norm = _norm(sc or "")
        inferred, hits = infer_chapter(sc_norm)
        # 一致性判定
        expected = EXPECTED_MAP.get(db_chap, [])
        if not hits:
            status = "UNKNOWN"
            n_unknown += 1
        elif inferred in expected:
            status = "OK"
            n_ok += 1
        else:
            # 主板章节 (main_board_profitable/unprofitable) 特例:
            # 如果命中 main_board 但同时命中 18A/18C/secondary, 是 mismatch
            # 如果只命中 main_board → 期望 main_board → OK
            if "main_board" in expected and "main_board" in hits and inferred == "main_board":
                status = "OK"
                n_ok += 1
            else:
                status = "MISMATCH"
                n_mismatch += 1
        by_status[(db_chap, inferred, status)] += 1
        report.append({
            "ipo_id": ipo_id,
            "stock_code": sc,
            "stock_code_normalized": sc_norm,
            "company_name_zh": name or "",
            "listing_date": str(ld)[:10] if ld else "",
            "db_chapter": db_chap,
            "inferred_chapter": inferred,
            "ifind_block_hits": "|".join(hits),
            "status": status,
        })

    # 5) 写报告
    rep_path = OUT_DIR / "verify_chapter_report.csv"
    mismatch_path = OUT_DIR / "verify_chapter_mismatch.csv"
    fields = ["ipo_id","stock_code","stock_code_normalized","company_name_zh",
              "listing_date","db_chapter","inferred_chapter","ifind_block_hits","status"]
    with open(rep_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in report:
            w.writerow(r)
    with open(mismatch_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in report:
            if r["status"] != "OK":
                w.writerow(r)

    # 6) 控制台摘要
    print(f"\n[step4] 验证结果 (共 {len(rows)} 只):")
    print(f"  ✓ OK       : {n_ok:>4d}  ({n_ok/len(rows)*100:.1f}%)")
    print(f"  ✗ MISMATCH : {n_mismatch:>4d}  ({n_mismatch/len(rows)*100:.1f}%)")
    print(f"  ? UNKNOWN  : {n_unknown:>4d}  ({n_unknown/len(rows)*100:.1f}%) (未在任一板块)")

    # 按 db_chapter 分组的 status 分布
    print(f"\n[step5] 按 db_chapter 分组]")
    by_chap_status = defaultdict(lambda: defaultdict(int))
    for r in report:
        by_chap_status[r["db_chapter"]][r["status"]] += 1
    print(f"  {'db_chapter':<28s} {'OK':>5s} {'MISMATCH':>9s} {'UNKNOWN':>8s} {'total':>6s}")
    for chap in sorted(by_chap_status.keys()):
        d = by_chap_status[chap]
        tot = sum(d.values())
        print(f"  {chap:<28s} {d.get('OK',0):>5d} {d.get('MISMATCH',0):>9d} {d.get('UNKNOWN',0):>8d} {tot:>6d}")

    # MISMATCH 明细 (按 db_chap → inferred 透视)
    if n_mismatch > 0:
        print(f"\n[step6] MISMATCH 类型分布:")
        mismatch_pivot = defaultdict(int)
        for r in report:
            if r["status"] == "MISMATCH":
                mismatch_pivot[(r["db_chapter"], r["inferred_chapter"])] += 1
        for (db_c, inf_c), n in sorted(mismatch_pivot.items(), key=lambda x: -x[1]):
            print(f"  db={db_c:<26s} → inferred={inf_c:<14s} n={n}")

    print(f"\n输出:")
    print(f"  全样本对照 → {rep_path}")
    print(f"  错分清单   → {mismatch_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
