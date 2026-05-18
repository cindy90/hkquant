"""拉取港股概念板块 (011007) 全量成分股, 建立 ipo_concepts 表 (1:N).

数据源:
    THS_DR('p03321','iv_bkid=011007', ...) → 223 个三级港股概念板块
    THS_DataPool('block', 'YYYY-MM-DD;{concept_id}', ...) → 每个概念的成分股

输出:
    outputs/concept_blocks.json           223 个概念的成分股缓存
    outputs/ipo_concepts_summary.csv     384 只 IPO 的概念列表 (人工抽检)
    outputs/concept_coverage.csv         每个概念的命中股票数 (人工抽检)
    DB.ipo_concepts 表                    1:N 关系表 (ipo_id, stock_code, concept_id, concept_name)

DB schema:
    CREATE TABLE ipo_concepts (
      ipo_id        TEXT NOT NULL,
      stock_code    TEXT NOT NULL,
      concept_id    TEXT NOT NULL,    -- 011007_xxxxx
      concept_name  TEXT NOT NULL,    -- "腾讯概念[HK]"
      data_date     TEXT,             -- 查询日期 YYYY-MM-DD
      PRIMARY KEY (ipo_id, concept_id)
    );
    CREATE INDEX idx_ipo_concepts_stock ON ipo_concepts(stock_code);
    CREATE INDEX idx_ipo_concepts_concept ON ipo_concepts(concept_id);

只追加新表, 不修改 ipo_master.

用法:
    python scripts/fetch_hk_concepts.py --dry-run     # 拉取 + 缓存, 不写库
    python scripts/fetch_hk_concepts.py               # 拉取 + 写库
    python scripts/fetch_hk_concepts.py --use-cache   # 复用 outputs/concept_blocks.json 写库
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sqlite3
import sys
import time
from collections import OrderedDict, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "nacs_real.db"
OUT_DIR = ROOT / "outputs"
CACHE = OUT_DIR / "concept_blocks.json"

sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _norm(code: str) -> str | None:
    """归一化股票代码; H 前缀的占位代码返回 None."""
    if not isinstance(code, str):
        return None
    c = code.strip()
    if c.startswith("H") and len(c) > 1 and c[1].isdigit():
        return None
    h, _, t = c.partition(".")
    h = h.lstrip("0") or "0"
    return h + ("." + t if t else "")


def relogin():
    from src.data_sources.ifind import market_env_fetcher as mef
    mef._LOGIN_OK = False
    mef.login_ifind()


def fetch_concept_tree() -> list[dict]:
    """返回 [{bid, name}, ...] 共 223 个三级概念板块."""
    from iFinDPy import THS_DR
    r = THS_DR(
        "p03321",
        "iv_bkid=011007",
        "p03321_f001:Y,p03321_f002:Y,p03321_f003:Y",
        "format:dataframe",
    )
    if r is None or getattr(r, "errorcode", -1) != 0 or r.data is None:
        raise RuntimeError(f"概念树拉取失败 ec={getattr(r, 'errorcode', '?')}")
    df = r.data.copy()
    df.columns = [c.lower() for c in df.columns]
    out = []
    for _, row in df.iterrows():
        try:
            lvl = int(row["p03321_f002"])
        except Exception:
            continue
        if lvl == 3:  # 三级 = 具体概念
            out.append({
                "bid": str(row["p03321_f003"]).strip(),
                "name": str(row["p03321_f001"]).strip(),
            })
    return out


def fetch_block(block_id: str, query_date: str) -> list[str]:
    from iFinDPy import THS_DataPool
    result = THS_DataPool(
        "block",
        f"{query_date};{block_id}",
        "date:Y,thscode:Y,security_name:Y",
    )
    if not isinstance(result, (dict, OrderedDict)):
        raise RuntimeError(f"DataPool 返回类型异常: {type(result)}")
    if result.get("errorcode") != 0:
        raise RuntimeError(f"ec={result.get('errorcode')} msg={result.get('errmsg')}")
    tables = result.get("tables")
    if not tables:
        return []
    t0 = tables[0]
    if not isinstance(t0, dict) or "table" not in t0:
        return []
    table = t0["table"]
    codes = table.get("THSCODE") or []
    out = []
    for sc in codes:
        sc_norm = _norm(str(sc).strip())
        if sc_norm is not None:
            out.append(sc_norm)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--batch-size", type=int, default=30)
    ap.add_argument("--sleep", type=float, default=0.4)
    ap.add_argument("--batch-sleep", type=float, default=2.0)
    ap.add_argument("--use-cache", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="拉取 + 缓存, 不写 DB")
    args = ap.parse_args()

    if not DB.exists():
        print(f"DB 不存在: {DB}")
        return 2
    OUT_DIR.mkdir(exist_ok=True)

    print("[step1] 登录 iFinD...")
    relogin()
    print("  ✓ OK")

    # 2) 拉概念树
    print("\n[step2] 拉取港股概念树 (011007)")
    concepts = fetch_concept_tree()
    print(f"  ✓ 共 {len(concepts)} 个三级概念")

    # 3) 拉成分 (或用缓存)
    block_members = {}  # bid → set(stock_code)
    if args.use_cache and CACHE.exists():
        with open(CACHE, "r", encoding="utf-8") as f:
            cached = json.load(f)
        for k, v in cached.items():
            block_members[k] = set(v)
        print(f"\n[step3] 使用缓存 {CACHE.name}: {len(block_members)} 个概念")
    else:
        print(f"\n[step3] 拉取 {len(concepts)} 个概念成分股 (batch={args.batch_size})")
        total = len(concepts)
        for batch_idx in range(0, total, args.batch_size):
            batch = concepts[batch_idx:batch_idx + args.batch_size]
            t0 = time.time()
            print(f"  --- 批次 {batch_idx//args.batch_size + 1} "
                  f"(rows {batch_idx+1}-{min(batch_idx+args.batch_size, total)}/{total}) ---")
            for n in batch:
                bid = n["bid"]
                name = n["name"]
                try:
                    members = fetch_block(bid, args.date)
                    block_members[bid] = set(members)
                    print(f"    ✓ {bid:<14s} {name:<22s} n={len(members)}")
                except Exception as e:
                    print(f"    ✗ {bid:<14s} {name:<22s} 失败: {e}; 重登重试")
                    try:
                        relogin()
                        members = fetch_block(bid, args.date)
                        block_members[bid] = set(members)
                        print(f"      ✓ 重试成功 n={len(members)}")
                    except Exception as e2:
                        print(f"      ✗ 重试仍失败: {e2}")
                        block_members[bid] = set()
                time.sleep(args.sleep)
            print(f"  批次耗时 {time.time()-t0:.1f}s")
            if batch_idx + args.batch_size < total:
                time.sleep(args.batch_sleep)
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump({k: sorted(v) for k, v in block_members.items()}, f,
                      ensure_ascii=False, indent=1)
        print(f"  ✓ 缓存 → {CACHE.name}")

    # 4) 反向匹配 ipo_master
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    rows = cur.execute("""
        SELECT ipo_id, stock_code, company_name_zh
        FROM ipo_master
    """).fetchall()
    print(f"\n[step4] ipo_master {len(rows)} 行, 反向匹配概念")

    # bid → name 映射
    bid_name = {n["bid"]: n["name"] for n in concepts}

    # 计算每只 IPO 命中的概念
    ipo_to_concepts = defaultdict(list)  # ipo_id → [(bid, name), ...]
    sc_to_ipo = {}
    for ipo_id, sc, name in rows:
        sc_norm = _norm(sc or "")
        if sc_norm:
            sc_to_ipo[sc_norm] = (ipo_id, sc, name)

    for bid, members in block_members.items():
        cname = bid_name.get(bid, "?")
        for sc_norm in members:
            if sc_norm in sc_to_ipo:
                ipo_id = sc_to_ipo[sc_norm][0]
                ipo_to_concepts[ipo_id].append((bid, cname))

    # 统计
    n_with_concepts = sum(1 for v in ipo_to_concepts.values() if v)
    avg_concepts = (sum(len(v) for v in ipo_to_concepts.values()) / max(n_with_concepts, 1))
    counts = sorted([len(v) for v in ipo_to_concepts.values()])
    n_zero = len(rows) - n_with_concepts
    print(f"  覆盖: {n_with_concepts}/{len(rows)} 只命中概念; 0 命中 {n_zero} 只")
    if counts:
        n = len(counts)
        print(f"  概念数量分布 (每只 IPO): "
              f"min={counts[0]} p25={counts[n//4]} p50={counts[n//2]} "
              f"p75={counts[(3*n)//4]} max={counts[-1]} avg={avg_concepts:.1f}")

    # 5) 概念覆盖统计 (每个概念命中多少只 IPO)
    concept_coverage = defaultdict(int)  # bid → n
    for clist in ipo_to_concepts.values():
        for bid, _ in clist:
            concept_coverage[bid] += 1

    # 6) 写两个 CSV
    summary_path = OUT_DIR / "ipo_concepts_summary.csv"
    coverage_path = OUT_DIR / "concept_coverage.csv"
    with open(summary_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["ipo_id", "stock_code", "company_name_zh", "n_concepts",
                    "concept_names", "concept_ids"])
        for ipo_id, sc, name in rows:
            clist = ipo_to_concepts.get(ipo_id, [])
            names = "|".join(c[1] for c in clist)
            bids = "|".join(c[0] for c in clist)
            w.writerow([ipo_id, sc, name or "", len(clist), names, bids])
    with open(coverage_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["concept_id", "concept_name", "n_ipo_hits", "block_total_size"])
        for c in concepts:
            n_hit = concept_coverage.get(c["bid"], 0)
            block_size = len(block_members.get(c["bid"], set()))
            w.writerow([c["bid"], c["name"], n_hit, block_size])
    print(f"  ✓ {summary_path.name}")
    print(f"  ✓ {coverage_path.name}")

    if args.dry_run:
        print("\n[dry-run] 不写 DB")
        conn.close()
        return 0

    # 7) 备份 DB
    bak = DB.with_suffix(DB.suffix + ".bak_concepts_" + time.strftime("%Y%m%d_%H%M%S"))
    shutil.copy2(DB, bak)
    print(f"\n[step5] 备份 → {bak.name}")

    # 8) 建表 + 写入
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ipo_concepts (
          ipo_id        TEXT NOT NULL,
          stock_code    TEXT NOT NULL,
          concept_id    TEXT NOT NULL,
          concept_name  TEXT NOT NULL,
          data_date     TEXT,
          PRIMARY KEY (ipo_id, concept_id)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ipo_concepts_stock ON ipo_concepts(stock_code)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ipo_concepts_concept ON ipo_concepts(concept_id)")
    # 清空再写 (幂等)
    cur.execute("DELETE FROM ipo_concepts")
    rows_to_insert = []
    for ipo_id, sc, _ in rows:
        for bid, cname in ipo_to_concepts.get(ipo_id, []):
            rows_to_insert.append((ipo_id, sc, bid, cname, args.date))
    cur.executemany(
        "INSERT INTO ipo_concepts (ipo_id, stock_code, concept_id, concept_name, data_date) "
        "VALUES (?, ?, ?, ?, ?)", rows_to_insert
    )
    conn.commit()
    print(f"\n[step6] 写入 ipo_concepts: {len(rows_to_insert)} 行")

    # 9) 验证
    n_total = cur.execute("SELECT COUNT(*) FROM ipo_concepts").fetchone()[0]
    n_distinct_ipo = cur.execute("SELECT COUNT(DISTINCT ipo_id) FROM ipo_concepts").fetchone()[0]
    n_distinct_concept = cur.execute("SELECT COUNT(DISTINCT concept_id) FROM ipo_concepts").fetchone()[0]
    print(f"  总行数: {n_total}")
    print(f"  涉及 IPO: {n_distinct_ipo}/{len(rows)}")
    print(f"  涉及概念: {n_distinct_concept}")
    print(f"\n  Top 10 概念 (按 IPO 命中数):")
    for r in cur.execute("""
        SELECT concept_name, COUNT(*) as n
        FROM ipo_concepts GROUP BY concept_id ORDER BY n DESC LIMIT 10
    """).fetchall():
        print(f"    {r[0]:<26s} n={r[1]}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
