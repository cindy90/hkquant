"""archive_outputs.py — 自动把 outputs/ 中的脚本产出归档到 data/。

设计原则
--------
1. **DB 是真相之源**：分类长表（ipo_concepts/ipo_industries）从 DB 导出，不依赖 outputs 副本
2. **三种归档模式**：
   - `overwrite`  字典 JSON、宽表/覆盖率 CSV → 直接覆盖最新版
   - `snapshot`   报告/IC/评分 → 加 `_YYYYMMDD` 日期后缀，已存在则跳过（保留历史）
   - `tree`       回测迭代文件夹 → copytree 到 iterations/<tag>/
3. **安全保证**：
   - 不动 `data/raw/`
   - 编码规范化只作用于归档后的目标 CSV（UTF-8 无 BOM + LF）
   - 所有动作写入 `data/_archive_manifest.csv` 审计
   - `--dry-run` 预演，全部打印不写盘
4. **幂等**：重复运行只补缺，不重复覆盖 snapshot

用法
----
    python scripts/archive_outputs.py --dry-run
    python scripts/archive_outputs.py
    python scripts/archive_outputs.py --date 20260509   # 指定快照日（默认今日）
    python scripts/archive_outputs.py --force-snapshot  # 强制覆盖 snapshot
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# 路径定位
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
OUTPUTS = ROOT / "outputs"
DATA = ROOT / "data"
DB_PATH = DATA / "nacs_real.db"
MANIFEST = DATA / "_archive_manifest.csv"

# ---------------------------------------------------------------------------
# 归档规则
#   每条 (mode, src, dst_dir, dst_name)
#   src 相对 outputs/，dst_dir 相对 data/，dst_name 用 {date} 占位日期
# ---------------------------------------------------------------------------
RULES = [
    # 字典 JSON（覆盖式）
    ("overwrite", "concept_blocks.json",             "dict", "hk_concept_blocks.json"),
    ("overwrite", "industry_blocks_sw.json",         "dict", "sw_industry_blocks.json"),
    ("overwrite", "industry_blocks_ths_global.json", "dict", "ths_global_industry_blocks.json"),
    ("overwrite", "verify_industry_blocks.json",     "dict", "hs_industry_blocks.json"),

    # 分类宽表（覆盖式，长表由 DB 导出，见 DB_EXPORTS）
    ("overwrite", "ipo_concepts_summary.csv",        "derived/ipo_classification", "ipo_concepts_wide.csv"),
    ("overwrite", "ipo_industries_summary.csv",      "derived/ipo_classification", "ipo_industries_wide.csv"),
    ("overwrite", "concept_coverage.csv",            "derived/ipo_classification", "concept_coverage.csv"),

    # 验证报告（快照）
    ("snapshot",  "verify_chapter_report.csv",       "derived/verification", "chapter_report_{date}.csv"),
    ("snapshot",  "verify_chapter_mismatch.csv",     "derived/verification", "chapter_mismatch_{date}.csv"),
    ("snapshot",  "verify_industry_report.csv",      "derived/verification", "industry_report_{date}.csv"),
    ("snapshot",  "verify_industry_mismatch.csv",    "derived/verification", "industry_mismatch_{date}.csv"),

    # IC 探索（快照）
    ("snapshot",  "peer_ic_results.csv",             "derived/peer_ic", "ic_results_{date}.csv"),
    ("snapshot",  "peer_ic_top.csv",                 "derived/peer_ic", "ic_top50_{date}.csv"),
    ("snapshot",  "peer_ic_robustness.csv",          "derived/peer_ic", "ic_robustness_{date}.csv"),
    ("snapshot",  "peer_ic_top_signals_detail.csv",  "derived/peer_ic", "ic_top_signals_detail_{date}.csv"),

    # NACS 评分（快照）
    ("snapshot",  "nacs_v7_scores.csv",              "derived/scores", "nacs_v7_scores_{date}.csv"),

    # 最新回测 IC 摘要（覆盖式 latest/）
    ("overwrite", "backtest_ic_static.json",         "derived/backtest/latest", "ic_static.json"),
    ("overwrite", "backtest_ic_realtime.json",       "derived/backtest/latest", "ic_realtime.json"),
]

# 回测迭代目录（tree mode），src 是文件夹名，dst tag = src 去掉 .csv 后缀
ITERATION_FOLDERS = [
    "backtest_p1_10.csv",
    "backtest_p1_11.csv",
    "backtest_p1_lockup_v2.csv",
    "backtest_p2_1.csv",
    "backtest_p2_2.csv",
]

# ---------------------------------------------------------------------------
# DB 导出（权威长表）
# ---------------------------------------------------------------------------
DB_EXPORTS = [
    {
        "table": "ipo_concepts",
        "out": "derived/ipo_classification/ipo_concepts.csv",
        "header": ["ipo_id", "stock_code", "concept_id", "concept_name", "data_date"],
        "sql": """SELECT ipo_id, stock_code, concept_id, concept_name, data_date
                  FROM ipo_concepts ORDER BY stock_code, concept_id""",
    },
    {
        "table": "ipo_industries",
        "out": "derived/ipo_classification/ipo_industries.csv",
        "header": ["ipo_id", "stock_code", "source", "l1_name", "l2_name", "l3_name",
                   "l4_name", "leaf_bid", "leaf_level", "data_date"],
        "sql": """SELECT ipo_id, stock_code, source, l1_name, l2_name, l3_name, l4_name,
                  leaf_bid, leaf_level, data_date
                  FROM ipo_industries ORDER BY stock_code, source""",
    },
]

# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def normalize_csv_inplace(path: Path) -> None:
    """把 CSV 转 UTF-8 无 BOM + LF。仅对归档目标。"""
    if path.suffix.lower() != ".csv":
        return
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    raw = raw.replace(b"\r\n", b"\n")
    path.write_bytes(raw)


def append_manifest(rows: list[dict]) -> None:
    """追加 manifest 记录。"""
    new_file = not MANIFEST.exists()
    with MANIFEST.open("a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["timestamp", "mode", "src", "dst", "size", "sha16", "status"])
        for r in rows:
            w.writerow([r["timestamp"], r["mode"], r["src"], r["dst"],
                        r["size"], r["sha16"], r["status"]])


# ---------------------------------------------------------------------------
# 单条规则处理
# ---------------------------------------------------------------------------

def archive_one(mode: str, src_rel: str, dst_dir_rel: str, dst_name: str | None,
                date: str, dry_run: bool, force: bool, ts: str) -> dict | None:
    src = OUTPUTS / src_rel
    if not src.exists():
        print(f"  [skip] 源不存在: outputs/{src_rel}")
        return None

    if mode == "tree":
        # 文件夹复制
        dst = DATA / dst_dir_rel
        action = f"copytree -> data/{dst_dir_rel}"
        if dst.exists() and not force:
            print(f"  [exist] {action}（已存在，跳过；--force-snapshot 可覆盖）")
            return {"timestamp": ts, "mode": mode, "src": f"outputs/{src_rel}",
                    "dst": f"data/{dst_dir_rel}", "size": 0, "sha16": "",
                    "status": "skipped"}
        if not dry_run:
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
            # 子 csv 编码规范化
            for p in dst.rglob("*.csv"):
                normalize_csv_inplace(p)
        size = sum(f.stat().st_size for f in src.rglob("*") if f.is_file())
        print(f"  [tree]  outputs/{src_rel}/ -> data/{dst_dir_rel}/  ({size}B)")
        return {"timestamp": ts, "mode": mode, "src": f"outputs/{src_rel}",
                "dst": f"data/{dst_dir_rel}", "size": size, "sha16": "",
                "status": "created" if not dry_run else "dry_run"}

    # 文件级
    dst_dir = DATA / dst_dir_rel
    dst_filename = (dst_name or src_rel).format(date=date)
    dst = dst_dir / dst_filename

    is_snapshot = mode == "snapshot"

    if dst.exists() and is_snapshot and not force:
        print(f"  [exist] data/{dst_dir_rel}/{dst_filename}（snapshot 已存在，--force-snapshot 强制覆盖）")
        return {"timestamp": ts, "mode": mode, "src": f"outputs/{src_rel}",
                "dst": f"data/{dst_dir_rel}/{dst_filename}",
                "size": dst.stat().st_size, "sha16": sha256(dst),
                "status": "skipped"}

    action = "overwrite" if mode == "overwrite" else "new snapshot"
    if dst.exists() and mode == "overwrite":
        action = "overwrite (existing)"

    if not dry_run:
        dst_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        normalize_csv_inplace(dst)

    size = dst.stat().st_size if dst.exists() else src.stat().st_size
    sha = sha256(dst) if dst.exists() and not dry_run else ""
    print(f"  [{mode:9s}] outputs/{src_rel:36s} -> data/{dst_dir_rel}/{dst_filename}  ({size}B)  {action}")
    return {"timestamp": ts, "mode": mode, "src": f"outputs/{src_rel}",
            "dst": f"data/{dst_dir_rel}/{dst_filename}", "size": size,
            "sha16": sha, "status": "created" if not dry_run else "dry_run"}


# ---------------------------------------------------------------------------
# DB 长表导出
# ---------------------------------------------------------------------------

def export_db_tables(date: str, dry_run: bool, ts: str) -> list[dict]:
    if not DB_PATH.exists():
        print(f"  [skip] DB 不存在: {DB_PATH}")
        return []
    rows_meta = []
    conn = sqlite3.connect(str(DB_PATH))
    try:
        for spec in DB_EXPORTS:
            out = DATA / spec["out"]
            cur = conn.execute(spec["sql"])
            data_rows = cur.fetchall()
            n = len(data_rows)
            if not dry_run:
                out.parent.mkdir(parents=True, exist_ok=True)
                with out.open("w", encoding="utf-8", newline="") as f:
                    w = csv.writer(f)
                    w.writerow(spec["header"])
                    for r in data_rows:
                        w.writerow(r)
                normalize_csv_inplace(out)
            size = out.stat().st_size if out.exists() else 0
            sha = sha256(out) if out.exists() and not dry_run else ""
            print(f"  [db-export] {spec['out']}  rows={n}  ({size}B)")
            rows_meta.append({"timestamp": ts, "mode": "db_export",
                              "src": f"DB:{spec['table']}",
                              "dst": "data/" + spec["out"],
                              "size": size, "sha16": sha,
                              "status": "created" if not dry_run else "dry_run"})
    finally:
        conn.close()
    return rows_meta


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="把 outputs/ 归档到 data/")
    ap.add_argument("--dry-run", action="store_true", help="预演，不写盘")
    ap.add_argument("--date", default=datetime.now().strftime("%Y%m%d"),
                    help="snapshot 日期后缀 (默认今日 YYYYMMDD)")
    ap.add_argument("--force-snapshot", action="store_true",
                    help="强制覆盖已存在的 snapshot（默认跳过）")
    args = ap.parse_args()

    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    ts = datetime.now().isoformat(timespec="seconds")
    print("=" * 78)
    print(f"archive_outputs  date={args.date}  dry_run={args.dry_run}  "
          f"force_snapshot={args.force_snapshot}")
    print(f"  ROOT    = {ROOT}")
    print(f"  OUTPUTS = {OUTPUTS}")
    print(f"  DATA    = {DATA}")
    print("=" * 78)

    if not OUTPUTS.exists():
        print(f"ERROR: outputs/ 不存在: {OUTPUTS}")
        return 1

    manifest_rows: list[dict] = []

    # 1) 单文件规则
    print("\n[1/3] 文件级归档")
    for mode, src, dst_dir, dst_name in RULES:
        rec = archive_one(mode, src, dst_dir, dst_name,
                          args.date, args.dry_run, args.force_snapshot, ts)
        if rec:
            manifest_rows.append(rec)

    # 2) 回测迭代目录
    print("\n[2/3] 回测迭代目录")
    for folder in ITERATION_FOLDERS:
        tag = folder.replace("backtest_", "").removesuffix(".csv")
        rec = archive_one("tree", folder, f"derived/backtest/iterations/{tag}",
                          None, args.date, args.dry_run, args.force_snapshot, ts)
        if rec:
            manifest_rows.append(rec)

    # 3) DB 长表导出
    print("\n[3/3] DB 权威长表导出")
    manifest_rows.extend(export_db_tables(args.date, args.dry_run, ts))

    # 写 manifest
    if not args.dry_run and manifest_rows:
        append_manifest(manifest_rows)
        print(f"\nmanifest -> data/_archive_manifest.csv  (+{len(manifest_rows)} 条)")

    # 汇总
    print("\n" + "=" * 78)
    summary = {"created": 0, "skipped": 0, "dry_run": 0, "failed": 0}
    for r in manifest_rows:
        summary[r["status"]] = summary.get(r["status"], 0) + 1
    print(f"汇总: {summary}")
    print("=" * 78)

    return 0


if __name__ == "__main__":
    sys.exit(main())
