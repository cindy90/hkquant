"""
run_realtime_env_ablation.py — A/B 跑两次回测 + 生成对比报告

流程:
    1. 跑 baseline:  python run_v7_backtest.py --use-static-env  -> outputs/backtest_ic_static.json
    2. 跑 realtime:  python run_v7_backtest.py                   -> outputs/backtest_ic_realtime.json
    3. 读两个 JSON, 写 reports/realtime_market_env_ablation.md

用法:
    python scripts/run_realtime_env_ablation.py
    python scripts/run_realtime_env_ablation.py --skip-static  (只跑 realtime, 复用现存 static)
    python scripts/run_realtime_env_ablation.py --skip-realtime
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "outputs"
REPORTS_DIR = ROOT / "reports"


def _run_backtest(static: bool) -> None:
    cmd = [sys.executable, str(ROOT / "run_v7_backtest.py")]
    if static:
        cmd.append("--use-static-env")
    print(f"\n>>> {'baseline (static)' if static else 'realtime'}: {' '.join(cmd)}")
    subprocess.run(cmd, cwd=str(ROOT), check=True)


def _fmt(v, fmt: str = "{:+.4f}") -> str:
    if v is None:
        return "—"
    try:
        return fmt.format(v)
    except (ValueError, TypeError):
        return "—"


def _delta(a, b):
    if a is None or b is None:
        return None
    return b - a


def _format_ic_table(static_data: dict, realtime_data: dict, label: str) -> str:
    lines = [f"## {label}", "",
             "| 期限 | Static | Realtime | Δ IC | n |",
             "|---|---|---|---|---|"]
    for key, term in [("5d", "5日"), ("30d", "30日"), ("60d", "60日"), ("180d", "180日")]:
        s = static_data.get(key, {})
        r = realtime_data.get(key, {})
        s_ic, r_ic = s.get("ic"), r.get("ic")
        d = _delta(s_ic, r_ic)
        lines.append(
            f"| {term} | {_fmt(s_ic)} | {_fmt(r_ic)} | "
            f"{_fmt(d, '{:+.4f}')} | {r.get('n', '—')} |"
        )
    lines.append("")
    return "\n".join(lines)


def _format_ls_table(static_data: dict, realtime_data: dict, label: str) -> str:
    lines = [f"## {label} L-S Spread (top-bot 20%)", "",
             "| 期限 | Static spread | Realtime spread | Δ |",
             "|---|---|---|---|"]
    for key, term in [("5d", "5日"), ("30d", "30日"), ("60d", "60日"), ("180d", "180日")]:
        s = static_data.get(key, {})
        r = realtime_data.get(key, {})
        s_sp, r_sp = s.get("ls_spread"), r.get("ls_spread")
        d = _delta(s_sp, r_sp)
        lines.append(
            f"| {term} | {_fmt(s_sp, '{:+.2%}')} | {_fmt(r_sp, '{:+.2%}')} | "
            f"{_fmt(d, '{:+.2%}')} |"
        )
    lines.append("")
    return "\n".join(lines)


def _cache_diagnostics(db_path: Path) -> str:
    if not db_path.exists():
        return "_(数据库不存在, 无法统计 cache)_\n"
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT source, COUNT(*) AS n FROM market_environment_cache
            GROUP BY source ORDER BY n DESC
        """).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM market_environment_cache"
        ).fetchone()
        fb_rows = conn.execute("""
            SELECT asof_month FROM market_environment_cache
            WHERE source='fallback' ORDER BY asof_month
        """).fetchall()
        conn.close()
        lines = [
            f"- market_environment_cache 行数: {total['n']}",
            "- source 分布:",
        ]
        for r in rows:
            lines.append(f"    - {r['source']}: {r['n']}")
        if fb_rows:
            months = ", ".join(str(r["asof_month"])[:7] for r in fb_rows)
            lines.append(f"- fallback 月份: {months}")
        return "\n".join(lines) + "\n"
    except sqlite3.OperationalError as e:
        return f"_(读取 cache 表失败: {e})_\n"


def build_markdown(ic_static: dict, ic_realtime: dict, db_path: Path) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    n_static = ic_static.get("n_total", "—")
    n_real = ic_realtime.get("n_total", "—")

    parts = [
        "# 实时市场数据接入消融实验 (Realtime MarketEnvironment Ablation)",
        "",
        f"生成时间: {now}",
        f"样本: Static n={n_static} / Realtime n={n_real}",
        "对比: `--use-static-env` (硬编码 baseline) vs 默认 (iFinD 月聚合 + DB 派生)",
        "",
    ]

    # 主板
    parts.append(_format_ic_table(
        ic_static.get("main_board", {}),
        ic_realtime.get("main_board", {}),
        "主板 IC (Spearman Rank)"
    ))
    parts.append(_format_ls_table(
        ic_static.get("main_board", {}),
        ic_realtime.get("main_board", {}),
        "主板"
    ))

    # Regime≥0 子集
    if ic_static.get("regime_pass") and ic_realtime.get("regime_pass"):
        parts.append(_format_ic_table(
            ic_static["regime_pass"],
            ic_realtime["regime_pass"],
            "Regime≥0 过滤后子集 IC"
        ))
        parts.append(_format_ls_table(
            ic_static["regime_pass"],
            ic_realtime["regime_pass"],
            "Regime≥0 过滤后子集"
        ))

    parts.append("## 缓存命中诊断")
    parts.append("")
    parts.append(_cache_diagnostics(db_path))

    # 结论
    s60 = ic_static.get("main_board", {}).get("60d", {}).get("ic")
    r60 = ic_realtime.get("main_board", {}).get("60d", {}).get("ic")
    delta_60 = _delta(s60, r60)
    parts.append("## 结论")
    parts.append("")
    if delta_60 is None:
        parts.append("- 数据不足, 无法判断 60d IC 变化")
    elif delta_60 >= 0.01:
        parts.append(f"- ✅ 60d IC 提升 {delta_60:+.4f} (≥ 目标 +0.01)")
    elif delta_60 > 0:
        parts.append(f"- 🟡 60d IC 提升 {delta_60:+.4f} (低于 +0.01 目标)")
    else:
        parts.append(f"- ❌ 60d IC 变化 {delta_60:+.4f} (未达预期)")
    parts.append("")
    return "\n".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description="MarketEnvironment 实时化消融实验")
    parser.add_argument("--skip-static", action="store_true",
                        help="跳过 baseline 跑 (复用现存 outputs/backtest_ic_static.json)")
    parser.add_argument("--skip-realtime", action="store_true",
                        help="跳过 realtime 跑 (复用现存 outputs/backtest_ic_realtime.json)")
    args = parser.parse_args()

    static_json = OUT_DIR / "backtest_ic_static.json"
    real_json = OUT_DIR / "backtest_ic_realtime.json"

    if not args.skip_static:
        _run_backtest(static=True)
    if not args.skip_realtime:
        _run_backtest(static=False)

    if not static_json.exists():
        print(f"❌ 缺少 {static_json}", file=sys.stderr)
        return 1
    if not real_json.exists():
        print(f"❌ 缺少 {real_json}", file=sys.stderr)
        return 1

    ic_static = json.loads(static_json.read_text(encoding="utf-8"))
    ic_realtime = json.loads(real_json.read_text(encoding="utf-8"))

    db_path = ROOT / "data" / "nacs_real.db"
    md = build_markdown(ic_static, ic_realtime, db_path)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out_md = REPORTS_DIR / "realtime_market_env_ablation.md"
    out_md.write_text(md, encoding="utf-8")

    s60 = ic_static.get("main_board", {}).get("60d", {}).get("ic")
    r60 = ic_realtime.get("main_board", {}).get("60d", {}).get("ic")
    delta = (r60 - s60) if (s60 is not None and r60 is not None) else None
    print(f"\n>>> 60d IC: {_fmt(s60)} → {_fmt(r60)} "
          f"(Δ {_fmt(delta, '{:+.4f}')})")
    print(f">>> 报告: {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
