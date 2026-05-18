"""peer IC 探索 第二轮: 稳健性核查 (分年份 + 净增量 + 分位实战回报).

针对第一轮 Top 信号:
    1. ths_global_l1 / top3_recent_mean / d30   (反转, IC ≈ -0.29)
    2. hs_l1 / top3_recent_mean / unlock_d90    (正向, IC ≈ +0.20, 净增量大)
    3. sw_l1 / top3_recent_mean / d1_close      (首日动量, IC ≈ +0.23)
    4. chapter / mean / m12                     (但与市场基线接近, 增量小)
    5. concept_any / mean / d30                 (反转, n 偏少)

核查项:
    A. 分年份 IC (看 2022/2023/2024/2025 是否稳定还是单年驱动)
    B. 净增量 IC (信号 IC - 市场基线同 window 同 pool IC)
    C. L-S 五分位实战收益 (Q1 vs Q5 mean return)
    D. 决策池子样: 仅 LARGE/TRIAL 的子样
    E. 敏感性: agg 切换 (mean vs top3 vs median)

输出:
    outputs/peer_ic_robustness.csv          每信号的稳健性指标
    outputs/peer_ic_top_signals_detail.csv  Top 信号的逐只 IPO 明细
    控制台 详细解读
"""
from __future__ import annotations

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

# 复用第一轮的工具
sys.path.insert(0, str(ROOT / "scripts"))
from explore_peer_industry_concept_ic import (
    WINDOW_DAYS, RETURN_COL, parse_date, spearman_ic, t_stat,
    quintile_ls_spread, aggregate, load_data, build_view_label, in_pool,
)


# 待核查的 Top 信号
SIGNALS = [
    # (label, view, agg, window, pool)
    ("S1_ths_global_l1_d30_top3",     "ths_global_l1", "top3_recent_mean", "d30",        "main_profitable"),
    ("S2_hs_l1_unlock_d90_top3",      "hs_l1",         "top3_recent_mean", "unlock_d90", "main_all"),
    ("S3_sw_l1_d1close_top3",         "sw_l1",         "top3_recent_mean", "d1_close",   "all"),
    ("S4_chapter_m12_mean",           "chapter",       "mean",             "m12",        "all"),
    ("S5_concept_any_d30_mean",       "concept_any",   "mean",             "d30",        "main_all"),
    ("S6_hs_l3_m12_median",           "hs_l3",         "median",           "m12",        "main_profitable"),
    ("S7_sw_l1_d30_top3",             "sw_l1",         "top3_recent_mean", "d30",        "all"),
    ("S8_ths_global_l1_d30_mean",     "ths_global_l1", "mean",             "d30",        "main_profitable"),
]


def compute_signal_xs_ys(masters, ipo_labels, view, agg, w, pool, min_peers=3):
    """对一个信号, 算每只 IPO 的 (X, Y, ipo_id, listing_year, listing_date).

    返回 list[dict]; 仅含 X 与 Y 都非 NaN 的行.
    """
    wnd = WINDOW_DAYS[w]
    ret_key = "r_" + RETURN_COL[w].replace("return_", "")

    ipos = list(masters.values())
    peer_pool = []
    for m in ipos:
        ld = m.get("listing_date")
        rv = m.get(ret_key)
        lbl = ipo_labels[m["ipo_id"]][view]
        if ld and (rv is not None) and lbl is not None:
            peer_pool.append((ld, lbl, rv, m["ipo_id"]))
    peer_pool.sort(key=lambda p: p[0])

    out = []
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
        if isinstance(my_lbl, frozenset):
            peers = [p for p in peer_pool if p[0] < cutoff
                     and isinstance(p[1], frozenset)
                     and (p[1] & my_lbl)
                     and p[3] != m["ipo_id"]]
        elif view == "market":
            peers = [p for p in peer_pool if p[0] < cutoff and p[3] != m["ipo_id"]]
        else:
            peers = [p for p in peer_pool if p[0] < cutoff and p[1] == my_lbl
                     and p[3] != m["ipo_id"]]
        if len(peers) < min_peers:
            continue
        peer_vals = [p[2] for p in peers]
        peer_dates = [p[0] for p in peers]
        x = aggregate(peer_vals, peer_dates, agg)
        if math.isnan(x):
            continue
        out.append({
            "ipo_id": m["ipo_id"],
            "stock_code": m["stock_code"],
            "name": m["name"],
            "chapter": m["chapter"],
            "listing_year": m["listing_date"].year if m["listing_date"] else None,
            "listing_date": m["listing_date"].isoformat() if m["listing_date"] else "",
            "pricing_date": m["pricing_date"].isoformat() if m["pricing_date"] else "",
            "X": x,
            "Y": rv,
            "n_peers": len(peers),
        })
    return out


def quintile_returns(xs, ys, q=5):
    """返回 [Q1_mean, Q2_mean, ..., Q5_mean] (按 X 升序分箱)."""
    n = len(xs)
    if n < q * 3:
        return [float("nan")] * q
    pairs = sorted(zip(xs, ys), key=lambda p: p[0])
    bin_size = n // q
    out = []
    for i in range(q):
        start = i * bin_size
        end = (i+1) * bin_size if i < q-1 else n
        bucket = [p[1] for p in pairs[start:end]]
        out.append(sum(bucket) / len(bucket) if bucket else float("nan"))
    return out


def main():
    print("[step1] 加载数据 + 预计算视图标签")
    masters, inds, concepts = load_data()
    ipo_labels = {}
    for ipo_id, m in masters.items():
        labels = {}
        for v in ["chapter", "hs_l1", "hs_l3", "ths_global_l1", "ths_global_l4",
                  "sw_l1", "sw_l3", "concept_any", "market"]:
            labels[v] = build_view_label(m, inds.get(ipo_id, {}),
                                         concepts.get(ipo_id, set()), v)
        ipo_labels[ipo_id] = labels
    print(f"  {len(masters)} 只 IPO")

    OUT_DIR.mkdir(exist_ok=True)
    rob_rows = []
    detail_rows = []

    print(f"\n[step2] 对每个信号做稳健性核查")
    for sig_label, view, agg, w, pool in SIGNALS:
        print(f"\n  ── {sig_label}: view={view} agg={agg} w={w} pool={pool}")
        rows = compute_signal_xs_ys(masters, ipo_labels, view, agg, w, pool)
        if len(rows) < 20:
            print(f"    样本不足: n={len(rows)}, 跳过")
            continue
        xs = [r["X"] for r in rows]
        ys = [r["Y"] for r in rows]
        ic, n = spearman_ic(xs, ys)
        t = t_stat(ic, n)
        ls, ls_t = quintile_ls_spread(xs, ys)
        # 五分位 mean Y
        qs = quintile_returns(xs, ys)
        # 市场基线 (同 w, 同 pool, view=market, agg=mean)
        base_rows = compute_signal_xs_ys(masters, ipo_labels, "market", "mean", w, pool)
        base_xs = [r["X"] for r in base_rows]
        base_ys = [r["Y"] for r in base_rows]
        base_ic, base_n = spearman_ic(base_xs, base_ys) if len(base_xs) >= 20 else (float("nan"), len(base_xs))
        delta_ic = ic - base_ic if not math.isnan(base_ic) else float("nan")

        # 分年份 IC
        by_year = defaultdict(lambda: {"x": [], "y": []})
        for r in rows:
            y_ = r["listing_year"]
            if y_:
                by_year[y_]["x"].append(r["X"])
                by_year[y_]["y"].append(r["Y"])
        year_ics = {}
        for yr, d in sorted(by_year.items()):
            if len(d["x"]) >= 8:
                yic, yn = spearman_ic(d["x"], d["y"])
                year_ics[yr] = (yic, yn)

        print(f"    n={n}, IC={ic:+.3f} (t={t:+.2f}), L-S={ls*100:+.1f}% (t={ls_t:+.2f})")
        print(f"    市场基线 IC = {base_ic:+.3f} (n={base_n}), 净增量 ΔIC = {delta_ic:+.3f}")
        print(f"    五分位 Y mean (Q1低X→Q5高X): "
              f"{' '.join(f'{q*100:+.1f}%' for q in qs)}")
        print(f"    分年份 IC:")
        for yr, (yic, yn) in year_ics.items():
            print(f"      {yr}: IC={yic:+.3f} n={yn}")

        rob_rows.append({
            "signal":        sig_label,
            "view":          view,
            "agg":           agg,
            "window":        w,
            "pool":          pool,
            "n_obs":         n,
            "ic":            ic,
            "t_stat":        t,
            "ls_spread":     ls,
            "ls_t_stat":     ls_t,
            "market_base_ic": base_ic,
            "delta_ic":      delta_ic,
            "Q1_mean":       qs[0] if len(qs)>=5 else float("nan"),
            "Q5_mean":       qs[4] if len(qs)>=5 else float("nan"),
            **{f"ic_{yr}": (year_ics.get(yr, (float('nan'), 0))[0]) for yr in [2022,2023,2024,2025]},
            **{f"n_{yr}": (year_ics.get(yr, (float('nan'), 0))[1]) for yr in [2022,2023,2024,2025]},
        })

        # 详细明细 (前 30 条)
        for r in rows:
            detail_rows.append({"signal": sig_label, **r})

    # 写 CSV
    rob_path = OUT_DIR / "peer_ic_robustness.csv"
    if rob_rows:
        fields = list(rob_rows[0].keys())
        with open(rob_path, "w", newline="", encoding="utf-8-sig") as f:
            wr = csv.DictWriter(f, fieldnames=fields)
            wr.writeheader()
            for r in rob_rows:
                wr.writerow(r)
        print(f"\n[step3] 稳健性 → {rob_path.name}")

    detail_path = OUT_DIR / "peer_ic_top_signals_detail.csv"
    if detail_rows:
        fields = ["signal","ipo_id","stock_code","name","chapter","listing_year",
                  "listing_date","pricing_date","X","Y","n_peers"]
        with open(detail_path, "w", newline="", encoding="utf-8-sig") as f:
            wr = csv.DictWriter(f, fieldnames=fields)
            wr.writeheader()
            for r in detail_rows:
                wr.writerow(r)
        print(f"  详细明细 → {detail_path.name}")

    # 总结排行
    print(f"\n[step4] 综合排行 (按 |ΔIC| 净增量, 排除市场基线效应):")
    rob_rows.sort(key=lambda r: -abs(r["delta_ic"]) if not math.isnan(r["delta_ic"]) else 0)
    print(f"  {'signal':<28s} {'n':>4} {'IC':>7} {'ΔIC':>7} {'L-S%':>7} {'Q1':>7} {'Q5':>7} {'分年稳定性'}")
    for r in rob_rows:
        # 看分年 IC 是否 ≥3 年同方向
        years_with_ic = [(y, r[f"ic_{y}"]) for y in [2022,2023,2024,2025]
                         if not math.isnan(r.get(f"ic_{y}", float('nan')))]
        if not years_with_ic:
            stable = "n/a"
        else:
            same_sign = sum(1 for _, yic in years_with_ic
                            if (yic > 0 and r["ic"] > 0) or (yic < 0 and r["ic"] < 0))
            stable = f"{same_sign}/{len(years_with_ic)}年同向"
        print(f"  {r['signal']:<28s} {r['n_obs']:>4d} "
              f"{r['ic']:>+6.2f} {r['delta_ic']:>+6.2f} "
              f"{r['ls_spread']*100:>+6.1f} "
              f"{r['Q1_mean']*100:>+6.1f} {r['Q5_mean']*100:>+6.1f} {stable}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
