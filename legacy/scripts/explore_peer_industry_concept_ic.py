"""peer 行业/概念聚合信号 IC 探索 (严格 look-ahead 防护).

设计:
    对每只目标 IPO i (有 listing_date, pricing_date, 真实回报 r_w_i):
        构造预测变量 X_i = aggregate( r_w_j for j ∈ peers(i, view) )
            其中 peers(i, view) = {j: j 与 i 共享 view 标签, 且 j.listing_date < cutoff_date(i, w)}
            cutoff_date(i, w) = i.pricing_date - WINDOW_DAYS[w]  (确保 peer 的 r_w 在 i 定价时已知)
        计算 IC = corr(X, Y) 跨样本.

    严格 look-ahead 防护:
        peer 的 listing_date 必须早于 target.pricing_date 减去窗口天数.

8 种 peer 视图 (view):
    chapter         — 同 listing_chapter (基线)
    hs_l1           — 同恒生一级行业
    hs_l3           — 同恒生三级行业
    ths_global_l1   — 同同花顺港股全球行业一级
    ths_global_l4   — 同同花顺港股全球行业四级 (末级)
    sw_l1           — 同申万港股一级
    sw_l3           — 同申万港股三级 (末级)
    concept_any     — 共享至少 1 个港股概念 (multi-tag)
    market          — 全市场 (无标签约束, 仅用于全样本基线对照)

4 种聚合 (agg):
    mean
    median
    top3_recent_mean   — 最近 3 个 peer (按 listing_date 降序) 的 mean
    n_peers            — peer 数量 (用作"流行度"代理)

7 种窗口 (w):
    d1_close
    d30
    m3
    m6
    m12
    unlock_d30
    unlock_d90

3 种样本池 (pool):
    all              — 全样本
    main_profitable  — listing_chapter=='main_board_profitable'
    main_all         — listing_chapter ∈ {main_board_profitable, main_board_unprofitable, a_plus_h}

约束:
    - peer 集合至少 N=3 (默认), 否则 X_i = NaN
    - IC 至少 N_obs=20 个有效观测才计算

输出:
    outputs/peer_ic_results.csv          完整 IC 表 (view × agg × w × pool)
    outputs/peer_ic_top.csv              按 |IC|×sqrt(n)×非零样本筛 Top 30 信号
    outputs/peer_ic_diagnostics.csv     每只 IPO × 信号 的 (X, Y) 明细 (Top 信号下钻)

只读, 不动 DB.
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import date, timedelta
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


# ============== 配置 ==============
WINDOW_DAYS = {
    "d1_close":   2,         # 首日 +1 天就知道
    "d30":        30,
    "m3":         90,
    "m6":         180,
    "m12":        365,
    "unlock_d30": 210,       # 6 月锁定 + 30 天 (主流)
    "unlock_d90": 270,       # 6 月锁定 + 90 天
}
RETURN_COL = {
    "d1_close":   "return_d1_close",
    "d30":        "return_d30",
    "m3":         "return_m3",
    "m6":         "return_m6",
    "m12":        "return_m12",
    "unlock_d30": "return_unlock_d30",
    "unlock_d90": "return_unlock_d90",
}
WINDOWS = list(WINDOW_DAYS.keys())
VIEWS = ["chapter", "hs_l1", "hs_l3",
         "ths_global_l1", "ths_global_l4",
         "sw_l1", "sw_l3",
         "concept_any", "market"]
AGGS = ["mean", "median", "top3_recent_mean", "n_peers"]
POOLS = ["all", "main_profitable", "main_all"]
MIN_PEERS = 3
MIN_OBS = 20


# ============== 工具 ==============
def parse_date(s) -> date | None:
    if not s:
        return None
    s = str(s)[:10]
    try:
        return date.fromisoformat(s)
    except Exception:
        return None


def spearman_ic(xs: list[float], ys: list[float]) -> tuple[float, int]:
    """Spearman rank correlation. xy 必须等长且无 NaN."""
    n = len(xs)
    if n < 2:
        return float("nan"), n
    # 求秩 (平均秩)
    def ranks(vs):
        idx = sorted(range(len(vs)), key=lambda i: vs[i])
        r = [0.0] * len(vs)
        i = 0
        while i < len(vs):
            j = i
            while j + 1 < len(vs) and vs[idx[j+1]] == vs[idx[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j+1):
                r[idx[k]] = avg
            i = j + 1
        return r
    rx, ry = ranks(xs), ranks(ys)
    mx = sum(rx) / n
    my = sum(ry) / n
    num = sum((rx[i]-mx)*(ry[i]-my) for i in range(n))
    dx = math.sqrt(sum((v-mx)**2 for v in rx))
    dy = math.sqrt(sum((v-my)**2 for v in ry))
    if dx == 0 or dy == 0:
        return float("nan"), n
    return num / (dx*dy), n


def t_stat(ic: float, n: int) -> float:
    if n < 3 or math.isnan(ic) or abs(ic) >= 1.0:
        return float("nan")
    return ic * math.sqrt((n-2) / (1 - ic*ic))


def quintile_ls_spread(xs, ys, q: int = 5) -> tuple[float, float]:
    """Long-Short: top quintile mean(y) - bottom quintile mean(y)."""
    n = len(xs)
    if n < q * 3:
        return float("nan"), float("nan")
    pairs = sorted(zip(xs, ys), key=lambda p: p[0])
    bin_size = n // q
    bot = [p[1] for p in pairs[:bin_size]]
    top = [p[1] for p in pairs[-bin_size:]]
    bot_mean = sum(bot) / len(bot) if bot else float("nan")
    top_mean = sum(top) / len(top) if top else float("nan")
    spread = top_mean - bot_mean
    # t-stat: 简单 two-sample t
    def var(v, m):
        if len(v) < 2:
            return float("nan")
        return sum((x-m)**2 for x in v) / (len(v)-1)
    vb, vt = var(bot, bot_mean), var(top, top_mean)
    if math.isnan(vb) or math.isnan(vt) or vb+vt == 0:
        return spread, float("nan")
    se = math.sqrt(vb/len(bot) + vt/len(top))
    if se == 0:
        return spread, float("nan")
    return spread, spread / se


def aggregate(values: list[float], dates: list[date], agg: str) -> float:
    if not values:
        return float("nan")
    if agg == "n_peers":
        return float(len(values))
    if agg == "mean":
        return sum(values) / len(values)
    if agg == "median":
        s = sorted(values)
        m = len(s) // 2
        return s[m] if len(s) % 2 == 1 else (s[m-1] + s[m]) / 2
    if agg == "top3_recent_mean":
        # 按 date 降序取最近 3
        order = sorted(range(len(values)), key=lambda i: dates[i], reverse=True)[:3]
        v = [values[i] for i in order]
        return sum(v) / len(v) if v else float("nan")
    return float("nan")


# ============== 主流程 ==============
def load_data():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    # ipo_master
    masters = {}
    for r in cur.execute("""
        SELECT ipo_id, stock_code, company_name_zh, listing_date, pricing_date,
               listing_chapter, gics_l2
        FROM ipo_master
    """).fetchall():
        ipo_id, sc, name, ld, pd_, ch, gics = r
        masters[ipo_id] = {
            "ipo_id": ipo_id,
            "stock_code": sc,
            "name": name or "",
            "listing_date": parse_date(ld),
            "pricing_date": parse_date(pd_),
            "chapter": ch or "",
            "gics_l2": gics or "",
        }
    # ipo_returns
    for r in cur.execute("""
        SELECT ipo_id, return_d1_close, return_d30, return_m3, return_m6,
               return_m12, return_unlock_d30, return_unlock_d90
        FROM ipo_returns
    """).fetchall():
        ipo_id = r[0]
        if ipo_id not in masters:
            continue
        masters[ipo_id]["r_d1_close"]   = r[1]
        masters[ipo_id]["r_d30"]        = r[2]
        masters[ipo_id]["r_m3"]         = r[3]
        masters[ipo_id]["r_m6"]         = r[4]
        masters[ipo_id]["r_m12"]        = r[5]
        masters[ipo_id]["r_unlock_d30"] = r[6]
        masters[ipo_id]["r_unlock_d90"] = r[7]
    # ipo_industries
    inds = defaultdict(dict)  # ipo_id → {source: row}
    for r in cur.execute("""
        SELECT ipo_id, source, l1_name, l2_name, l3_name, l4_name, leaf_bid
        FROM ipo_industries
    """).fetchall():
        ipo_id, src, l1, l2, l3, l4, leaf = r
        inds[ipo_id][src] = {"l1": l1, "l2": l2, "l3": l3, "l4": l4, "leaf": leaf}
    # ipo_concepts
    concepts = defaultdict(set)
    for r in cur.execute("""
        SELECT ipo_id, concept_id FROM ipo_concepts
    """).fetchall():
        concepts[r[0]].add(r[1])
    conn.close()
    return masters, inds, concepts


def build_view_label(m: dict, inds_m: dict, concepts_m: set, view: str) -> object:
    """返回该 IPO 在该视图下的标签 (str 或 frozenset).

    None / "" 表示无标签 (不参与匹配).
    """
    if view == "chapter":
        return m.get("chapter") or None
    if view == "hs_l1":
        # 解析 gics_l2 = "一级(HS)-..." 取一级
        g = m.get("gics_l2") or ""
        if not g:
            return None
        l1 = g.split("-")[0] if "-" in g else g
        # 去掉 (HS)
        return l1.replace("(HS)", "").strip() or None
    if view == "hs_l3":
        return m.get("gics_l2") or None  # 完整三级路径
    if view == "ths_global_l1":
        return inds_m.get("ths_global", {}).get("l1") or None
    if view == "ths_global_l4":
        x = inds_m.get("ths_global", {})
        return x.get("leaf") or x.get("l4") or None
    if view == "sw_l1":
        return inds_m.get("sw", {}).get("l1") or None
    if view == "sw_l3":
        x = inds_m.get("sw", {})
        return x.get("leaf") or x.get("l3") or None
    if view == "concept_any":
        return frozenset(concepts_m) if concepts_m else None
    if view == "market":
        return "ALL"
    return None


def in_pool(m: dict, pool: str) -> bool:
    if pool == "all":
        return True
    ch = m.get("chapter") or ""
    if pool == "main_profitable":
        return ch == "main_board_profitable"
    if pool == "main_all":
        return ch in ("main_board_profitable", "main_board_unprofitable", "a_plus_h")
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-peers", type=int, default=MIN_PEERS)
    ap.add_argument("--min-obs", type=int, default=MIN_OBS)
    args = ap.parse_args()

    OUT_DIR.mkdir(exist_ok=True)
    print("[step1] 加载数据")
    masters, inds, concepts = load_data()
    print(f"  ipo_master: {len(masters)}")
    print(f"  ipo_industries: {sum(len(v) for v in inds.values())}")
    print(f"  ipo_concepts: {sum(len(v) for v in concepts.values())}")

    # 预先为每只 IPO 计算 view 标签
    print("\n[step2] 预计算视图标签")
    ipo_labels = {}  # ipo_id → {view: label}
    for ipo_id, m in masters.items():
        labels = {}
        for v in VIEWS:
            labels[v] = build_view_label(m, inds.get(ipo_id, {}), concepts.get(ipo_id, set()), v)
        ipo_labels[ipo_id] = labels

    # 对每只 IPO, 准备 (listing_date, pricing_date) 用于 look-ahead
    ipos = list(masters.values())
    n_total = len(ipos)
    print(f"\n[step3] 计算 IC (views={len(VIEWS)} × aggs={len(AGGS)} × windows={len(WINDOWS)} × pools={len(POOLS)})")
    print(f"  约束: min_peers={args.min_peers}, min_obs={args.min_obs}")

    results = []
    diag_rows = []  # 详细诊断 (仅 Top 信号)

    # 对每个 (view, agg, w, pool) 组合循环
    total_combo = len(VIEWS) * len(AGGS) * len(WINDOWS) * len(POOLS)
    combo_idx = 0
    for view in VIEWS:
        for w in WINDOWS:
            wnd = WINDOW_DAYS[w]
            ret_key = "r_" + RETURN_COL[w].replace("return_", "")
            # peer 候选: 必须有该窗口的真实回报 + 有 listing_date + 有该 view 的标签
            peer_pool = []  # [(listing_date, label, return_w)]
            for m in ipos:
                ld = m.get("listing_date")
                rv = m.get(ret_key)
                lbl = ipo_labels[m["ipo_id"]][view]
                if ld and (rv is not None) and lbl is not None:
                    peer_pool.append((ld, lbl, rv, m["ipo_id"]))
            # 按 ld 排序
            peer_pool.sort(key=lambda p: p[0])

            for agg in AGGS:
                for pool in POOLS:
                    combo_idx += 1
                    xs, ys, used_ipos = [], [], []
                    for m in ipos:
                        if not in_pool(m, pool):
                            continue
                        pd_ = m.get("pricing_date")
                        rv = m.get(ret_key)
                        if pd_ is None or rv is None:
                            continue
                        cutoff = pd_ - timedelta(days=wnd)
                        my_lbl = ipo_labels[m["ipo_id"]][view]
                        if my_lbl is None:
                            continue
                        # 找 peers: listing_date < cutoff and 共享标签
                        if isinstance(my_lbl, frozenset):  # concept_any
                            peers = [p for p in peer_pool if p[0] < cutoff
                                     and isinstance(p[1], frozenset)
                                     and (p[1] & my_lbl)
                                     and p[3] != m["ipo_id"]]
                        elif view == "market":
                            peers = [p for p in peer_pool if p[0] < cutoff
                                     and p[3] != m["ipo_id"]]
                        else:
                            peers = [p for p in peer_pool if p[0] < cutoff
                                     and p[1] == my_lbl
                                     and p[3] != m["ipo_id"]]
                        if len(peers) < args.min_peers:
                            continue
                        peer_vals = [p[2] for p in peers]
                        peer_dates = [p[0] for p in peers]
                        x = aggregate(peer_vals, peer_dates, agg)
                        if math.isnan(x):
                            continue
                        xs.append(x)
                        ys.append(rv)
                        used_ipos.append(m["ipo_id"])
                    if len(xs) < args.min_obs:
                        results.append({
                            "view": view, "agg": agg, "window": w, "pool": pool,
                            "n_obs": len(xs), "ic": float("nan"), "t_stat": float("nan"),
                            "ls_spread": float("nan"), "ls_t_stat": float("nan"),
                        })
                        continue
                    ic, n = spearman_ic(xs, ys)
                    t = t_stat(ic, n)
                    ls, ls_t = quintile_ls_spread(xs, ys)
                    results.append({
                        "view": view, "agg": agg, "window": w, "pool": pool,
                        "n_obs": n, "ic": ic, "t_stat": t,
                        "ls_spread": ls, "ls_t_stat": ls_t,
                    })
                    if combo_idx % 30 == 0:
                        print(f"  进度 {combo_idx}/{total_combo}: "
                              f"{view}/{agg}/{w}/{pool} n={n} ic={ic:+.3f} t={t:+.2f}")

    # 4) 写完整 CSV
    rep_path = OUT_DIR / "peer_ic_results.csv"
    fields = ["view", "agg", "window", "pool", "n_obs", "ic", "t_stat", "ls_spread", "ls_t_stat"]
    with open(rep_path, "w", newline="", encoding="utf-8-sig") as f:
        wr = csv.DictWriter(f, fieldnames=fields)
        wr.writeheader()
        for row in results:
            wr.writerow(row)
    print(f"\n[step4] 全表 → {rep_path.name} ({len(results)} 行)")

    # 5) Top 30 (按 |t_stat| 排序, 排除 nan & n_peers 聚合)
    valid = [r for r in results
             if not math.isnan(r["ic"]) and not math.isnan(r["t_stat"])
             and r["agg"] != "n_peers" and r["view"] != "market"]
    valid.sort(key=lambda r: -abs(r["t_stat"]))
    top_path = OUT_DIR / "peer_ic_top.csv"
    with open(top_path, "w", newline="", encoding="utf-8-sig") as f:
        wr = csv.DictWriter(f, fieldnames=fields)
        wr.writeheader()
        for row in valid[:50]:
            wr.writerow(row)
    print(f"  Top50 → {top_path.name}")

    # 6) Top 30 控制台展示
    print(f"\n[step5] Top 30 信号 (按 |t-stat| 降序, 排除 market 基线 + n_peers)")
    print(f"  {'view':<14} {'agg':<18} {'window':<11} {'pool':<16} {'n':>4} {'ic':>7} {'t':>6} {'ls%':>7} {'ls_t':>5}")
    for r in valid[:30]:
        print(f"  {r['view']:<14} {r['agg']:<18} {r['window']:<11} {r['pool']:<16} "
              f"{r['n_obs']:>4d} {r['ic']:>+7.3f} {r['t_stat']:>+6.2f} "
              f"{(r['ls_spread']*100 if not math.isnan(r['ls_spread']) else 0):>+6.1f}% "
              f"{(r['ls_t_stat'] if not math.isnan(r['ls_t_stat']) else 0):>+5.2f}")

    # 7) market 基线 (供对比)
    print(f"\n[step6] 全市场基线 (view=market):")
    base = [r for r in results if r["view"] == "market" and r["agg"] == "mean"
            and not math.isnan(r["ic"])]
    base.sort(key=lambda r: (r["pool"], r["window"]))
    print(f"  {'window':<11} {'pool':<16} {'n':>4} {'ic':>7} {'t':>6}")
    for r in base:
        print(f"  {r['window']:<11} {r['pool']:<16} {r['n_obs']:>4d} {r['ic']:>+7.3f} {r['t_stat']:>+6.2f}")

    # 8) 按 view 平均 IC 摘要
    print(f"\n[step7] 各 view 在 m6+main_profitable 下的 mean IC:")
    print(f"  {'view':<14} {'agg':<18} {'n':>4} {'ic':>7} {'t':>6}")
    for r in results:
        if r["window"] == "m6" and r["pool"] == "main_profitable" and r["agg"] == "mean":
            if not math.isnan(r["ic"]):
                print(f"  {r['view']:<14} {r['agg']:<18} "
                      f"{r['n_obs']:>4d} {r['ic']:>+7.3f} {r['t_stat']:>+6.2f}")

    # 9) 各 view 各 window 主板 IC 矩阵
    print(f"\n[step8] view × window IC 矩阵 (pool=main_profitable, agg=mean):")
    print(f"  {'view':<16} ", end="")
    for w in WINDOWS:
        print(f" {w:>11}", end="")
    print()
    for v in VIEWS:
        print(f"  {v:<16} ", end="")
        for w in WINDOWS:
            row = next((r for r in results
                        if r["view"]==v and r["window"]==w
                        and r["pool"]=="main_profitable" and r["agg"]=="mean"), None)
            if row and not math.isnan(row["ic"]):
                print(f" {row['ic']:>+5.2f}(t={row['t_stat']:>+4.1f})", end="")
            else:
                print(f" {'-':>11}", end="")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
