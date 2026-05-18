"""验证 ipo_master.gics_l2 (恒生行业三级分类) 与 iFinD 板块成分一致性.

数据源:
    1) THS_DR('p03321','iv_bkid=011002', ...) 拉取恒生行业树 (12 一级 / N 二级 / 108 三级)
    2) THS_DataPool('block', 'YYYY-MM-DD;{l5_id}', ...) 拉取每个三级行业成分股
       按 30 个三级行业一批, 每批之间 sleep 防限流

匹配规则:
    对每只港股 sc, 在 108 个三级行业成分中查找所属三级行业, 并向上推断二级/一级.
    一只股票可能命中多个三级 (恒生分类原则上互斥, 极少多归; 取第一命中并提示).

DB 字段:
    ipo_master.gics_l2 格式 "一级(HS)-二级(HS)-三级(HS)".
    例: "资讯科技业(HS)-软件服务(HS)-互联网及在线服务(HS)"

输出:
    outputs/verify_industry_report.csv      全样本对照 (384 行)
    outputs/verify_industry_mismatch.csv    错分清单
    outputs/verify_industry_blocks.json     108 个三级行业成分缓存 (复用)
    控制台 summary

只读 (不动 DB).

用法:
    python scripts/verify_industry_via_ifind.py
    python scripts/verify_industry_via_ifind.py --batch-size 30
    python scripts/verify_industry_via_ifind.py --use-cache   # 复用 verify_industry_blocks.json
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
import sys
import time
from collections import OrderedDict, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "nacs_real.db"
OUT_DIR = ROOT / "outputs"
CACHE = OUT_DIR / "verify_industry_blocks.json"

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


def _strip_hs(s: str) -> str:
    """去掉 (HS) 后缀, 用于对比."""
    return re.sub(r"\s*\(HS\)\s*", "", s or "").strip()


def relogin():
    from src.data_sources.ifind import market_env_fetcher as mef
    mef._LOGIN_OK = False
    mef.login_ifind()


def fetch_industry_tree() -> list[dict]:
    """返回恒生行业树, 每条 {bid, name, level, parent_path}.

    parent_path 为从一级到当前层的中文名拼接 (用 '|' 分隔).
    只返回 level=3,4,5 (一二三级).
    """
    from iFinDPy import THS_DR
    r = THS_DR(
        "p03321",
        "iv_bkid=011002",
        "p03321_f001:Y,p03321_f002:Y,p03321_f003:Y",
        "format:dataframe",
    )
    if r is None or getattr(r, "errorcode", -1) != 0 or r.data is None:
        raise RuntimeError(f"恒生行业树拉取失败 ec={getattr(r, 'errorcode', '?')}")
    df = r.data.copy()
    df.columns = [c.lower() for c in df.columns]
    nodes = []
    for _, row in df.iterrows():
        name = str(row["p03321_f001"]).strip()
        try:
            lvl = int(row["p03321_f002"])
        except Exception:
            continue
        bid = str(row["p03321_f003"]).strip()
        if lvl in (3, 4, 5):
            nodes.append({"bid": bid, "name": name, "level": lvl})
    # 推断父路径: 按 bid 前缀关系
    by_bid = {n["bid"]: n for n in nodes}
    for n in nodes:
        bid = n["bid"]
        # bid 格式有两类:
        #   1) 数字嵌套: 011002005001003 (12位+3位+3位)  → 父 = bid[:-3]
        #   2) 011002HSxxxxxxx (带 HS 标记的新版 5级)    → 父 = 找最长前缀匹配的 4 级
        path = []
        if "HS" not in bid[6:]:
            cur = bid
            while cur and cur != "011002":
                if cur in by_bid:
                    path.append(by_bid[cur]["name"])
                cur = cur[:-3]
        else:
            # 新版 HSxxxxxxx 不严格遵循 bid 前缀; 按 prefix 中数字段匹配 4级
            # 011002HS101015 → HS 前的 011002 (顶), 后面 HS101015 不可拆
            # 退而求其次: name 里找不到对应一级二级, 用 bid 中前几位匹配同前缀的 4 级
            # 或简单留空, 后面对比时只比三级名称
            path.append(n["name"])  # placeholder
        path.reverse()
        n["path"] = path  # path[0]=一级 path[1]=二级 path[2]=三级
    return nodes


def fetch_block(block_id: str, query_date: str) -> list[str]:
    """拉取板块成分, 返回归一化代码列表 (去 H 占位)."""
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
    ap.add_argument("--batch-size", type=int, default=30,
                    help="每批拉取多少个三级行业 (默认 30)")
    ap.add_argument("--sleep", type=float, default=0.4)
    ap.add_argument("--batch-sleep", type=float, default=2.0)
    ap.add_argument("--use-cache", action="store_true",
                    help="复用 outputs/verify_industry_blocks.json")
    args = ap.parse_args()

    if not DB.exists():
        print(f"DB 不存在: {DB}")
        return 2
    OUT_DIR.mkdir(exist_ok=True)

    print(f"[step1] 登录 iFinD...")
    relogin()
    print(f"  ✓ 登录 OK")

    # 2) 拉取恒生行业树
    print(f"\n[step2] 拉取恒生行业树 (011002)")
    nodes = fetch_industry_tree()
    n_l3 = sum(1 for n in nodes if n["level"] == 3)
    n_l4 = sum(1 for n in nodes if n["level"] == 4)
    n_l5 = sum(1 for n in nodes if n["level"] == 5)
    print(f"  ✓ 一级={n_l3} 二级={n_l4} 三级={n_l5}")
    by_bid = {n["bid"]: n for n in nodes}

    # 3) 构建 三级 bid → (一级, 二级, 三级) 路径映射
    #    数字嵌套结构: 三级 bid 长度 15, 父=bid[:-3] (二级), 祖=bid[:-6] (一级)
    #    新版 HS 结构: 011002HSxxxxxx, 父级关系不通过 bid 前缀, 使用查询接口逐个查
    print(f"\n[step3] 解析三级行业父路径")
    l5_paths = {}  # bid → (l1_name, l2_name, l3_name)
    for n in nodes:
        if n["level"] != 5:
            continue
        bid = n["bid"]
        l3_name = n["name"]
        if "HS" not in bid[6:]:
            l4_bid = bid[:-3]
            l3_bid = bid[:-6]
            l1_name = by_bid.get(l3_bid, {}).get("name", "?")
            l2_name = by_bid.get(l4_bid, {}).get("name", "?")
            l5_paths[bid] = (l1_name, l2_name, l3_name)
        else:
            # 新版 HS 5级: 用 THS_DR 查询其父
            # 简化处理: 后面单独查
            l5_paths[bid] = ("?", "?", l3_name)

    # 修补 HS 新版 5级 的父路径: 用 p03321 查询 iv_bkid=父bid (向上一级)
    # 实际上 p03321 接口可以查询 当前 bid 自身, 但要查父需要遍历. 这里采用反向方式:
    # 对每个一级 bid, 重新调用 p03321 拉取其下所有节点, 关联 4级与 5级 (含 HS).
    print(f"  → 修补 HS 新版 5级父路径...")
    from iFinDPy import THS_DR
    l1_nodes = [n for n in nodes if n["level"] == 3]
    for l1 in l1_nodes:
        try:
            r = THS_DR(
                "p03321",
                f"iv_bkid={l1['bid']}",
                "p03321_f001:Y,p03321_f002:Y,p03321_f003:Y",
                "format:dataframe",
            )
            if r is None or getattr(r, "errorcode", -1) != 0 or r.data is None:
                continue
            df = r.data.copy()
            df.columns = [c.lower() for c in df.columns]
            sub = df[df["p03321_f002"].astype(int).isin([4, 5])].copy()
            # sub 是 l1 下所有 4/5 级节点, 按出现顺序: 通常 4级A, 然后A下5级们, 然后4级B...
            # 用顺序推断父子: 维护 current_l4_name
            current_l4 = None
            for _, row in sub.iterrows():
                lvl = int(row["p03321_f002"])
                bid = str(row["p03321_f003"]).strip()
                name = str(row["p03321_f001"]).strip()
                if lvl == 4:
                    current_l4 = name
                elif lvl == 5 and "HS" in bid[6:]:
                    # 修补 HS 新版
                    l5_paths[bid] = (l1["name"], current_l4 or "?", name)
        except Exception as e:
            print(f"  ✗ 修补 {l1['name']} 失败: {e}")
        time.sleep(0.2)

    # 4) 分批拉取 108 个三级行业成分
    l5_list = [n for n in nodes if n["level"] == 5]
    print(f"\n[step4] 拉取 {len(l5_list)} 个三级行业成分股 (batch={args.batch_size})")

    block_members = {}  # bid → set(stock_code)
    if args.use_cache and CACHE.exists():
        with open(CACHE, "r", encoding="utf-8") as f:
            cached = json.load(f)
        for k, v in cached.items():
            block_members[k] = set(v)
        print(f"  ✓ 使用缓存 {CACHE.name}: {len(block_members)} 个三级行业")
    else:
        total = len(l5_list)
        for batch_idx in range(0, total, args.batch_size):
            batch = l5_list[batch_idx:batch_idx + args.batch_size]
            t0 = time.time()
            print(f"  --- 批次 {batch_idx//args.batch_size + 1} "
                  f"(rows {batch_idx+1}-{min(batch_idx+args.batch_size, total)}/{total}) ---")
            for n in batch:
                bid = n["bid"]
                name = n["name"]
                try:
                    members = fetch_block(bid, args.date)
                    block_members[bid] = set(members)
                    print(f"    ✓ {bid:<16s} {name:<22s} n={len(members)}")
                except Exception as e:
                    print(f"    ✗ {bid:<16s} {name:<22s} 失败: {e}; 重登重试")
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
        # 缓存
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump({k: sorted(v) for k, v in block_members.items()}, f,
                      ensure_ascii=False, indent=1)
        print(f"  ✓ 缓存 → {CACHE.name}")

    # 5) 反向匹配 ipo_master
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    rows = cur.execute("""
        SELECT ipo_id, stock_code, company_name_zh, gics_l2, listing_chapter
        FROM ipo_master ORDER BY listing_date
    """).fetchall()
    conn.close()
    print(f"\n[step5] ipo_master 共 {len(rows)} 行")

    def infer_industry(sc_norm: str) -> tuple[list[str], list[tuple[str, str, str]]]:
        """返回 (命中的三级 bid 列表, 命中的路径列表 [(l1,l2,l3),...])."""
        hits = []
        paths = []
        for bid, members in block_members.items():
            if sc_norm in members:
                hits.append(bid)
                paths.append(l5_paths.get(bid, ("?", "?", "?")))
        return hits, paths

    report = []
    n_ok = n_mismatch = n_unknown = n_multihit = 0
    for ipo_id, sc, name, gics_l2_db, ch in rows:
        sc_norm = _norm(sc or "")
        if sc_norm is None:
            continue
        hits_bid, paths = infer_industry(sc_norm)
        # 解析 DB 字段
        db_parts = [_strip_hs(p) for p in (gics_l2_db or "").split("-")]
        db_l1 = db_parts[0] if len(db_parts) > 0 else ""
        db_l2 = db_parts[1] if len(db_parts) > 1 else ""
        db_l3 = db_parts[2] if len(db_parts) > 2 else ""

        if not hits_bid:
            status = "UNKNOWN"
            n_unknown += 1
            inferred_l1 = inferred_l2 = inferred_l3 = ""
            inferred_bids = ""
        else:
            if len(hits_bid) > 1:
                n_multihit += 1
            # 取第一命中
            inferred_l1, inferred_l2, inferred_l3 = paths[0]
            inferred_bids = "|".join(hits_bid)
            # 一致性: 三级名称匹配即 OK; 否则 MISMATCH
            if db_l3 and db_l3 == inferred_l3:
                status = "OK"
                n_ok += 1
            elif any(db_l3 == p[2] for p in paths):
                # DB 三级在多命中之一
                status = "OK"
                n_ok += 1
            else:
                status = "MISMATCH"
                n_mismatch += 1

        report.append({
            "ipo_id": ipo_id,
            "stock_code": sc,
            "company_name_zh": name or "",
            "listing_chapter": ch or "",
            "db_l1": db_l1,
            "db_l2": db_l2,
            "db_l3": db_l3,
            "inferred_l1": inferred_l1 if hits_bid else "",
            "inferred_l2": inferred_l2 if hits_bid else "",
            "inferred_l3": inferred_l3 if hits_bid else "",
            "inferred_bids": inferred_bids,
            "n_hits": len(hits_bid),
            "status": status,
        })

    # 6) 写报告
    rep_path = OUT_DIR / "verify_industry_report.csv"
    mismatch_path = OUT_DIR / "verify_industry_mismatch.csv"
    fields = ["ipo_id","stock_code","company_name_zh","listing_chapter",
              "db_l1","db_l2","db_l3",
              "inferred_l1","inferred_l2","inferred_l3",
              "inferred_bids","n_hits","status"]
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

    # 7) 控制台
    n_total = len(report)
    print(f"\n[step6] 验证结果 (共 {n_total} 只):")
    print(f"  ✓ OK       : {n_ok:>4d}  ({n_ok/n_total*100:.1f}%)")
    print(f"  ✗ MISMATCH : {n_mismatch:>4d}  ({n_mismatch/n_total*100:.1f}%)")
    print(f"  ? UNKNOWN  : {n_unknown:>4d}  ({n_unknown/n_total*100:.1f}%)")
    print(f"  ⚠ 多重命中  : {n_multihit:>4d}")

    # MISMATCH 透视: 按 (db_l1, inferred_l1) 分组
    if n_mismatch > 0:
        print(f"\n[step7] MISMATCH 类型分布 (一级行业流向):")
        pivot = defaultdict(int)
        for r in report:
            if r["status"] == "MISMATCH":
                pivot[(r["db_l1"], r["inferred_l1"])] += 1
        for (db_c, inf_c), n in sorted(pivot.items(), key=lambda x: -x[1])[:20]:
            print(f"  db={db_c:<14s} → inferred={inf_c:<14s} n={n}")

    print(f"\n输出:")
    print(f"  全样本对照 → {rep_path}")
    print(f"  错分清单   → {mismatch_path}")
    print(f"  行业缓存   → {CACHE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
