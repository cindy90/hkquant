"""拉取申万港股行业 (011008) + 同花顺港股全球行业 (011003), 建 ipo_industries 表.

数据源:
    THS_DR('p03321','iv_bkid={root}', ...) → 行业树
    THS_DataPool('block', date;{leaf_id}, ...) → 末级成分股

数据范围:
    011003 同花顺港股全球行业: 11 一级 / 25 二级 / 74 三级 / 163 四级 (末级)
    011008 港股申万行业:       31 一级 / 134 二级 / 346 三级 (末级)
    合计 509 个末级 block, 按 30/批 = 17 批

存储 (新表, 不动 ipo_master):
    CREATE TABLE ipo_industries (
      ipo_id        TEXT NOT NULL,
      stock_code    TEXT NOT NULL,
      source        TEXT NOT NULL,    -- 'sw' | 'ths_global'
      l1_name       TEXT,
      l2_name       TEXT,
      l3_name       TEXT,
      l4_name       TEXT,             -- 仅 ths_global 有 4 级
      leaf_bid      TEXT NOT NULL,
      leaf_level    INTEGER NOT NULL, -- 5(sw 三级)/ 6(ths 四级)
      data_date     TEXT,
      PRIMARY KEY (ipo_id, source)
    );

用法:
    python scripts/fetch_hk_industries.py                # 全量拉取 + 写库
    python scripts/fetch_hk_industries.py --dry-run      # 仅缓存
    python scripts/fetch_hk_industries.py --use-cache    # 复用缓存写库
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
CACHE_SW = OUT_DIR / "industry_blocks_sw.json"
CACHE_THS = OUT_DIR / "industry_blocks_ths_global.json"

sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _norm(code: str) -> str | None:
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


def fetch_tree(root_bid: str) -> list[dict]:
    """拉取整棵树, 返回所有节点 [{bid, name, level}]."""
    from iFinDPy import THS_DR
    r = THS_DR(
        "p03321",
        f"iv_bkid={root_bid}",
        "p03321_f001:Y,p03321_f002:Y,p03321_f003:Y",
        "format:dataframe",
    )
    if r is None or getattr(r, "errorcode", -1) != 0 or r.data is None:
        raise RuntimeError(f"行业树 {root_bid} 拉取失败 ec={getattr(r, 'errorcode', '?')}")
    df = r.data.copy()
    df.columns = [c.lower() for c in df.columns]
    out = []
    for _, row in df.iterrows():
        try:
            lvl = int(row["p03321_f002"])
        except Exception:
            continue
        out.append({
            "bid": str(row["p03321_f003"]).strip(),
            "name": str(row["p03321_f001"]).strip(),
            "level": lvl,
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


def build_path_map(nodes: list[dict], leaf_level: int) -> dict[str, list[str]]:
    """构建 leaf_bid → [l1, l2, l3, ...] 路径 (按 bid 前缀嵌套).

    适用于纯数字 bid (无 HS 标记). 申万和同花顺港股全球均为数字 bid.
    """
    by_bid = {n["bid"]: n for n in nodes}
    paths = {}
    for n in nodes:
        if n["level"] != leaf_level:
            continue
        bid = n["bid"]
        # bid 长度: 一级 9, 二级 12, 三级 15, 四级 18 (每级 +3 数字)
        path = [n["name"]]
        cur = bid[:-3]
        while cur and len(cur) >= 9 and cur in by_bid:
            path.insert(0, by_bid[cur]["name"])
            cur = cur[:-3]
        paths[bid] = path
    return paths


def fetch_all_blocks(leaves: list[dict], cache_path: Path, args) -> dict[str, set]:
    """拉取所有末级成分 (或读缓存)."""
    block_members = {}
    if args.use_cache and cache_path.exists():
        with open(cache_path, "r", encoding="utf-8") as f:
            cached = json.load(f)
        for k, v in cached.items():
            block_members[k] = set(v)
        print(f"  ✓ 使用缓存 {cache_path.name}: {len(block_members)}")
        return block_members

    total = len(leaves)
    for batch_idx in range(0, total, args.batch_size):
        batch = leaves[batch_idx:batch_idx + args.batch_size]
        t0 = time.time()
        print(f"  --- 批次 {batch_idx//args.batch_size + 1} "
              f"(rows {batch_idx+1}-{min(batch_idx+args.batch_size, total)}/{total}) ---")
        for n in batch:
            bid = n["bid"]
            name = n["name"]
            try:
                members = fetch_block(bid, args.date)
                block_members[bid] = set(members)
                print(f"    ✓ {bid:<20s} {name:<22s} n={len(members)}")
            except Exception as e:
                print(f"    ✗ {bid:<20s} {name:<22s} 失败: {e}; 重登重试")
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

    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump({k: sorted(v) for k, v in block_members.items()}, f,
                  ensure_ascii=False, indent=1)
    print(f"  ✓ 缓存 → {cache_path.name}")
    return block_members


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--batch-size", type=int, default=30)
    ap.add_argument("--sleep", type=float, default=0.4)
    ap.add_argument("--batch-sleep", type=float, default=2.0)
    ap.add_argument("--use-cache", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not DB.exists():
        print(f"DB 不存在: {DB}")
        return 2
    OUT_DIR.mkdir(exist_ok=True)

    print("[step1] 登录 iFinD...")
    relogin()
    print("  ✓ OK")

    # 2) 拉两棵树
    print("\n[step2a] 拉同花顺港股全球行业树 (011003)")
    ths_nodes = fetch_tree("011003")
    ths_leaves = [n for n in ths_nodes if n["level"] == 6]
    ths_paths = build_path_map(ths_nodes, leaf_level=6)
    print(f"  ✓ 节点 {len(ths_nodes)} (末级 6 = {len(ths_leaves)} 个)")

    print("\n[step2b] 拉港股申万行业树 (011008)")
    sw_nodes = fetch_tree("011008")
    sw_leaves = [n for n in sw_nodes if n["level"] == 5]
    sw_paths = build_path_map(sw_nodes, leaf_level=5)
    print(f"  ✓ 节点 {len(sw_nodes)} (末级 5 = {len(sw_leaves)} 个)")

    # 3) 拉成分
    print(f"\n[step3a] 拉同花顺末级成分 ({len(ths_leaves)} 个, batch={args.batch_size})")
    ths_blocks = fetch_all_blocks(ths_leaves, CACHE_THS, args)

    print(f"\n[step3b] 拉申万末级成分 ({len(sw_leaves)} 个, batch={args.batch_size})")
    sw_blocks = fetch_all_blocks(sw_leaves, CACHE_SW, args)

    # 4) 反向匹配
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    rows = cur.execute("""
        SELECT ipo_id, stock_code, company_name_zh
        FROM ipo_master
    """).fetchall()
    print(f"\n[step4] ipo_master {len(rows)} 行")

    sc_to_ipo = {}
    for ipo_id, sc, name in rows:
        sc_norm = _norm(sc or "")
        if sc_norm:
            sc_to_ipo[sc_norm] = (ipo_id, sc, name)

    def reverse_match(blocks, paths, leaf_level, source_label):
        """返回 ipo_id → list of {leaf_bid, leaf_level, path[1:N]}."""
        ipo_hits = defaultdict(list)
        multi_hit = 0
        for bid, members in blocks.items():
            path = paths.get(bid, [])
            for sc_norm in members:
                if sc_norm in sc_to_ipo:
                    ipo_id = sc_to_ipo[sc_norm][0]
                    ipo_hits[ipo_id].append({
                        "leaf_bid": bid,
                        "leaf_level": leaf_level,
                        "path": path,
                    })
        for ipo_id, hits in ipo_hits.items():
            if len(hits) > 1:
                multi_hit += 1
        n_with = sum(1 for v in ipo_hits.values() if v)
        print(f"  [{source_label}] 命中 IPO {n_with}/{len(rows)} (多重命中 {multi_hit})")
        return ipo_hits

    ths_ipo_hits = reverse_match(ths_blocks, ths_paths, 6, "ths_global")
    sw_ipo_hits = reverse_match(sw_blocks, sw_paths, 5, "sw")

    # 多重命中统计
    print("\n[step5] 多重命中诊断")
    for label, ipo_hits in [("ths_global", ths_ipo_hits), ("sw", sw_ipo_hits)]:
        multi = [(k, v) for k, v in ipo_hits.items() if len(v) > 1]
        if multi:
            print(f"  [{label}] {len(multi)} 只多重命中, 取第一命中:")
            for ipo_id, hits in multi[:5]:
                sc = sc_to_ipo[_norm(next(r[1] for r in rows if r[0] == ipo_id))][1] if False else "?"
                names = [h["path"][-1] if h["path"] else h["leaf_bid"] for h in hits]
                print(f"    {ipo_id}: {names}")

    # 5) 输出 CSV
    summary_path = OUT_DIR / "ipo_industries_summary.csv"
    with open(summary_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["ipo_id", "stock_code", "company_name_zh",
                    "ths_global_path", "ths_global_leaf_bid",
                    "sw_path", "sw_leaf_bid"])
        for ipo_id, sc, name in rows:
            ths = ths_ipo_hits.get(ipo_id, [])
            sw = sw_ipo_hits.get(ipo_id, [])
            ths_path = " | ".join(ths[0]["path"]) if ths else ""
            ths_bid = ths[0]["leaf_bid"] if ths else ""
            sw_path = " | ".join(sw[0]["path"]) if sw else ""
            sw_bid = sw[0]["leaf_bid"] if sw else ""
            w.writerow([ipo_id, sc, name or "", ths_path, ths_bid, sw_path, sw_bid])
    print(f"\n[step6] CSV → {summary_path.name}")

    if args.dry_run:
        print("\n[dry-run] 不写 DB")
        conn.close()
        return 0

    # 6) 备份 + 建表 + 写入
    bak = DB.with_suffix(DB.suffix + ".bak_industries_" + time.strftime("%Y%m%d_%H%M%S"))
    shutil.copy2(DB, bak)
    print(f"\n[step7] 备份 → {bak.name}")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS ipo_industries (
          ipo_id        TEXT NOT NULL,
          stock_code    TEXT NOT NULL,
          source        TEXT NOT NULL,
          l1_name       TEXT,
          l2_name       TEXT,
          l3_name       TEXT,
          l4_name       TEXT,
          leaf_bid      TEXT NOT NULL,
          leaf_level    INTEGER NOT NULL,
          data_date     TEXT,
          PRIMARY KEY (ipo_id, source)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ipo_industries_stock ON ipo_industries(stock_code)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ipo_industries_leaf ON ipo_industries(leaf_bid)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ipo_industries_l1 ON ipo_industries(l1_name)")
    cur.execute("DELETE FROM ipo_industries")

    rows_to_insert = []
    for ipo_id, sc, _ in rows:
        for source, ipo_hits, leaf_level in [
            ("ths_global", ths_ipo_hits, 6),
            ("sw",          sw_ipo_hits,  5),
        ]:
            hits = ipo_hits.get(ipo_id, [])
            if not hits:
                continue
            h = hits[0]  # 取第一命中
            p = h["path"]
            l1 = p[0] if len(p) > 0 else None
            l2 = p[1] if len(p) > 1 else None
            l3 = p[2] if len(p) > 2 else None
            l4 = p[3] if len(p) > 3 else None
            rows_to_insert.append((
                ipo_id, sc, source, l1, l2, l3, l4,
                h["leaf_bid"], h["leaf_level"], args.date
            ))
    cur.executemany(
        "INSERT INTO ipo_industries (ipo_id, stock_code, source, "
        "l1_name, l2_name, l3_name, l4_name, leaf_bid, leaf_level, data_date) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows_to_insert
    )
    conn.commit()
    print(f"\n[step8] 写入 ipo_industries: {len(rows_to_insert)} 行")

    # 7) 验证
    print(f"\n  按 source 统计:")
    for r in cur.execute("""
        SELECT source, COUNT(*) FROM ipo_industries GROUP BY source
    """).fetchall():
        print(f"    {r[0]:<12s} n={r[1]}")
    print(f"\n  Top 8 申万一级行业 (n IPO):")
    for r in cur.execute("""
        SELECT l1_name, COUNT(*) FROM ipo_industries WHERE source='sw'
        GROUP BY l1_name ORDER BY COUNT(*) DESC LIMIT 8
    """).fetchall():
        print(f"    {r[0]:<14s} n={r[1]}")
    print(f"\n  Top 8 同花顺全球行业一级 (n IPO):")
    for r in cur.execute("""
        SELECT l1_name, COUNT(*) FROM ipo_industries WHERE source='ths_global'
        GROUP BY l1_name ORDER BY COUNT(*) DESC LIMIT 8
    """).fetchall():
        print(f"    {r[0]:<14s} n={r[1]}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
