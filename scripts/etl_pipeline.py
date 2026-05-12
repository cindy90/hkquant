"""
ETL 管道编排: load → fix → verify → quality report

设计目标:
    把散落的 load_to_db / fix_* / build_*_cache 脚本串成一条可重跑的管道.
    每步记录 exit_code + 耗时, 全部结束后输出执行摘要.

步骤:
    1. load    — CSV → DB (data_sources.ifind.load_to_db)
    2. fix     — 数据质量修复 (fix_p1_data_quality, fix_p0_pricing_date, etc.)
    3. verify  — 关键约束校验 (字段非空率, FK 完整性)
    4. quality — 刷新 data_quality_score + 输出 JSON 报告
    5. cache   — 派生缓存重建 (build_market_env_cache_partial, build_ipo_returns_cache)

用法:
    python scripts/etl_pipeline.py                   # 全量执行
    python scripts/etl_pipeline.py --dry-run          # 只读模式 (传递给各子步骤)
    python scripts/etl_pipeline.py --steps load,fix   # 选择性执行
    python scripts/etl_pipeline.py --skip-ifind       # 跳过需要 iFinD 的步骤
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from log import get_logger, setup_cli_logging  # noqa: E402
from data.dao import db_connect  # noqa: E402
from data.data_quality import (  # noqa: E402
    refresh_quality_scores,
    generate_quality_report,
    save_quality_report,
)

_log = get_logger("etl_pipeline")

DEFAULT_DB = ROOT / "data" / "nacs_real.db"
DEFAULT_RAW_DIR = ROOT / "data" / "raw" / "ifind"

ALL_STEPS = ("load", "fix", "verify", "quality", "cache")


# =============================================================================
# Step result tracking
# =============================================================================

@dataclass
class StepResult:
    name: str
    status: str = "pending"     # pending / ok / warn / fail / skip
    message: str = ""
    elapsed_s: float = 0.0


@dataclass
class PipelineResult:
    steps: List[StepResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(s.status in ("ok", "skip") for s in self.steps)

    def summary(self) -> str:
        lines = ["ETL Pipeline Summary", "=" * 50]
        for s in self.steps:
            tag = {"ok": "OK", "warn": "WARN", "fail": "FAIL",
                   "skip": "SKIP", "pending": "----"}.get(s.status, "?")
            lines.append(f"  [{tag:4s}] {s.name:12s}  {s.elapsed_s:6.1f}s  {s.message}")
        lines.append("=" * 50)
        overall = "SUCCESS" if self.ok else "FAILED"
        lines.append(f"  Overall: {overall}")
        return "\n".join(lines)


# =============================================================================
# Step implementations
# =============================================================================

def _step_load(db_path: Path, raw_dir: Path, *, dry_run: bool) -> StepResult:
    """Step 1: CSV → DB via load_to_db."""
    result = StepResult(name="load")
    t0 = time.time()
    try:
        from data_sources.ifind.load_to_db import (
            load_ipo_info, load_cornerstones, load_delisted,
        )
        from data.schema import init_database

        if not db_path.exists() and not dry_run:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            init_database(str(db_path))
            _log.info("schema initialized: %s", db_path)

        conn_target = str(db_path) if db_path.exists() else ":memory:"
        with db_connect(conn_target) as conn:
            if conn_target == ":memory:":
                from data.schema import SCHEMA_SQL
                conn.executescript(SCHEMA_SQL)

            # IPO info
            csv_ipo = raw_dir / "ifind_ipo_info.csv"
            if csv_ipo.exists():
                s = load_ipo_info(conn, csv_ipo, dry_run=dry_run)
                _log.info("ipo_info: csv=%d upserted=%d skip=%d",
                          s.n_rows_csv, s.n_inserted,
                          s.n_skipped_no_date + s.n_skipped_no_code)
            else:
                _log.warning("ipo_info CSV 不存在: %s", csv_ipo)

            # Cornerstones
            csv_cs = raw_dir / "ifind_cornerstones.csv"
            if csv_cs.exists():
                s2 = load_cornerstones(conn, csv_cs, dry_run=dry_run)
                _log.info("cornerstones: csv=%d new=%d links=%d",
                          s2.n_rows_csv, s2.n_cs_new, s2.n_links_inserted)
            else:
                _log.warning("cornerstones CSV 不存在: %s", csv_cs)

            # Delisted
            csv_del = raw_dir / "ifind_delisted_hk.csv"
            if csv_del.exists():
                s3 = load_delisted(conn, csv_del, dry_run=dry_run)
                _log.info("delisted: csv=%d matched=%d", s3.n_rows_csv, s3.n_matched)

        result.status = "ok"
        result.message = "loaded"
    except FileNotFoundError as e:
        result.status = "fail"
        result.message = str(e)
    except Exception as e:
        result.status = "fail"
        result.message = f"{type(e).__name__}: {e}"
        _log.error("load step failed: %s", e, exc_info=True)
    result.elapsed_s = time.time() - t0
    return result


def _step_fix(db_path: Path, *, dry_run: bool) -> StepResult:
    """Step 2: Data quality fixes (in-DB patches)."""
    result = StepResult(name="fix")
    t0 = time.time()
    if not db_path.exists():
        result.status = "skip"
        result.message = "DB 不存在"
        result.elapsed_s = time.time() - t0
        return result

    fixes_applied = 0
    warnings = []
    try:
        with db_connect(str(db_path)) as conn:
            # Fix #3: pricing_in_range 重算
            if not dry_run:
                cur = conn.execute("""
                    UPDATE ipo_master
                    SET pricing_in_range = ROUND(
                        (offer_price_hkd - offer_price_low) * 1.0
                        / NULLIF(offer_price_high - offer_price_low, 0), 4)
                    WHERE offer_price_hkd IS NOT NULL
                      AND offer_price_high IS NOT NULL
                      AND offer_price_low IS NOT NULL
                      AND offer_price_high != offer_price_low
                """)
                fixes_applied += cur.rowcount
                _log.info("pricing_in_range recalc: %d rows", cur.rowcount)

            # Fix #4: greenshoe_pct 异常值置 NULL
            if not dry_run:
                cur = conn.execute("""
                    UPDATE ipo_master
                    SET greenshoe_pct = NULL
                    WHERE greenshoe_pct > 0.30
                """)
                if cur.rowcount > 0:
                    _log.info("greenshoe_pct outliers nullified: %d", cur.rowcount)
                    fixes_applied += cur.rowcount

            # Fix #5: pe_at_offer 截尾
            if not dry_run:
                cur = conn.execute("""
                    UPDATE ipo_master
                    SET pe_at_offer = NULL
                    WHERE pe_at_offer < -100 OR pe_at_offer > 200
                """)
                if cur.rowcount > 0:
                    _log.info("pe_at_offer outliers nullified: %d", cur.rowcount)
                    fixes_applied += cur.rowcount

        result.status = "ok"
        result.message = f"{fixes_applied} fixes"
    except Exception as e:
        result.status = "fail"
        result.message = f"{type(e).__name__}: {e}"
        _log.error("fix step failed: %s", e, exc_info=True)
    result.elapsed_s = time.time() - t0
    return result


def _step_verify(db_path: Path) -> StepResult:
    """Step 3: 关键约束校验."""
    result = StepResult(name="verify")
    t0 = time.time()
    if not db_path.exists():
        result.status = "skip"
        result.message = "DB 不存在"
        result.elapsed_s = time.time() - t0
        return result

    issues: List[str] = []
    try:
        with db_connect(str(db_path)) as conn:
            # 1. ipo_master 行数
            n_ipo = conn.execute("SELECT COUNT(*) FROM ipo_master").fetchone()[0]
            _log.info("ipo_master: %d rows", n_ipo)
            if n_ipo == 0:
                issues.append("ipo_master 为空")

            # 2. 孤立 link (ipo_id 在 link 中存在但 ipo_master 中不存在)
            orphan_links = conn.execute("""
                SELECT COUNT(*) FROM ipo_cornerstone_link l
                WHERE NOT EXISTS (
                    SELECT 1 FROM ipo_master m WHERE m.ipo_id = l.ipo_id
                )
            """).fetchone()[0]
            if orphan_links > 0:
                issues.append(f"ipo_cornerstone_link 有 {orphan_links} 行孤立 (ipo_id 无对应)")
                _log.warning("orphan links: %d", orphan_links)

            # 3. listing_date NOT NULL 检查
            null_dates = conn.execute(
                "SELECT COUNT(*) FROM ipo_master WHERE listing_date IS NULL"
            ).fetchone()[0]
            if null_dates > 0:
                issues.append(f"ipo_master {null_dates} 行 listing_date 为 NULL")

            # 4. cornerstone_master 别名覆盖率
            cs_total = conn.execute("SELECT COUNT(*) FROM cornerstone_master").fetchone()[0]
            cs_no_alias = conn.execute("""
                SELECT COUNT(*) FROM cornerstone_master cm
                WHERE NOT EXISTS (
                    SELECT 1 FROM cornerstone_aliases ca
                    WHERE ca.cornerstone_id = cm.cornerstone_id
                )
            """).fetchone()[0]
            if cs_no_alias > 0:
                issues.append(f"cornerstone_master {cs_no_alias}/{cs_total} 无别名")
                _log.warning("cornerstones without alias: %d/%d", cs_no_alias, cs_total)

        if issues:
            result.status = "warn"
            result.message = "; ".join(issues)
        else:
            result.status = "ok"
            result.message = f"{n_ipo} IPOs, {cs_total} CS, 0 issues"

    except Exception as e:
        result.status = "fail"
        result.message = f"{type(e).__name__}: {e}"
        _log.error("verify step failed: %s", e, exc_info=True)
    result.elapsed_s = time.time() - t0
    return result


def _step_quality(db_path: Path, *, dry_run: bool) -> StepResult:
    """Step 4: 刷新 data_quality_score + 输出 JSON."""
    result = StepResult(name="quality")
    t0 = time.time()
    if not db_path.exists():
        result.status = "skip"
        result.message = "DB 不存在"
        result.elapsed_s = time.time() - t0
        return result

    try:
        with db_connect(str(db_path)) as conn:
            if not dry_run:
                n = refresh_quality_scores(conn)
                report = generate_quality_report(conn)
                save_quality_report(report)
                avg = report.get("avg_quality_score") or 0
                result.message = f"avg={avg:.4f}, {n} rows updated"
            else:
                report = generate_quality_report(conn)
                avg = report.get("avg_quality_score") or 0
                result.message = f"avg={avg:.4f} (dry-run)"
        result.status = "ok"
    except Exception as e:
        result.status = "fail"
        result.message = f"{type(e).__name__}: {e}"
        _log.error("quality step failed: %s", e, exc_info=True)
    result.elapsed_s = time.time() - t0
    return result


def _step_cache(db_path: Path, *, dry_run: bool, skip_ifind: bool) -> StepResult:
    """Step 5: 派生缓存重建 (market env + returns)."""
    result = StepResult(name="cache")
    t0 = time.time()
    if not db_path.exists():
        result.status = "skip"
        result.message = "DB 不存在"
        result.elapsed_s = time.time() - t0
        return result

    if skip_ifind:
        result.status = "skip"
        result.message = "skip_ifind=True (cache build 需要 iFinD)"
        result.elapsed_s = time.time() - t0
        return result

    # cache build 脚本依赖 iFinD, 在 CI 环境中不可用
    # 仅在本地 iFinD 环境中执行
    try:
        from data_sources.ifind.http_client import is_ifind_available
        if not is_ifind_available():
            result.status = "skip"
            result.message = "iFinD 不可用 (本地客户端未注册)"
            result.elapsed_s = time.time() - t0
            return result
    except ImportError:
        result.status = "skip"
        result.message = "iFinD http_client 不可用"
        result.elapsed_s = time.time() - t0
        return result

    result.status = "skip"
    result.message = "cache build 通过独立脚本执行 (build_market_env_cache.py / build_ipo_returns_cache.py)"
    result.elapsed_s = time.time() - t0
    return result


# =============================================================================
# Pipeline orchestrator
# =============================================================================

def run_pipeline(
    *,
    db_path: Path = DEFAULT_DB,
    raw_dir: Path = DEFAULT_RAW_DIR,
    steps: Optional[List[str]] = None,
    dry_run: bool = False,
    skip_ifind: bool = False,
) -> PipelineResult:
    """执行 ETL 管道."""
    active_steps = steps or list(ALL_STEPS)
    pipeline = PipelineResult()

    step_map = {
        "load": lambda: _step_load(db_path, raw_dir, dry_run=dry_run),
        "fix": lambda: _step_fix(db_path, dry_run=dry_run),
        "verify": lambda: _step_verify(db_path),
        "quality": lambda: _step_quality(db_path, dry_run=dry_run),
        "cache": lambda: _step_cache(db_path, dry_run=dry_run, skip_ifind=skip_ifind),
    }

    for step_name in ALL_STEPS:
        if step_name not in active_steps:
            pipeline.steps.append(StepResult(name=step_name, status="skip",
                                             message="not selected"))
            continue

        _log.info("=== Step: %s ===", step_name)
        sr = step_map[step_name]()
        pipeline.steps.append(sr)
        _log.info("[%s] %s — %s (%.1fs)", sr.status.upper(), sr.name,
                  sr.message, sr.elapsed_s)

        # fail 不中断后续步骤 (除了 load 失败后 fix/verify/quality 没意义)
        if sr.status == "fail" and step_name == "load":
            _log.error("load 失败, 跳过后续依赖步骤")
            for remaining in ALL_STEPS[ALL_STEPS.index(step_name) + 1:]:
                if remaining in active_steps:
                    pipeline.steps.append(StepResult(
                        name=remaining, status="skip",
                        message="skipped (load failed)"))
            break

    return pipeline


# =============================================================================
# CLI
# =============================================================================

def main() -> int:
    setup_cli_logging("INFO")

    ap = argparse.ArgumentParser(
        description="NACS ETL Pipeline — load → fix → verify → quality → cache"
    )
    ap.add_argument("--db", default=str(DEFAULT_DB),
                    help=f"SQLite 路径 (默认: {DEFAULT_DB})")
    ap.add_argument("--raw-dir", default=str(DEFAULT_RAW_DIR),
                    help=f"CSV 目录 (默认: {DEFAULT_RAW_DIR})")
    ap.add_argument("--steps", default="all",
                    help=f"逗号分隔的步骤名 (默认: all = {ALL_STEPS})")
    ap.add_argument("--dry-run", action="store_true",
                    help="只读模式")
    ap.add_argument("--skip-ifind", action="store_true",
                    help="跳过需要 iFinD 客户端的步骤 (cache)")
    args = ap.parse_args()

    steps = (list(ALL_STEPS) if args.steps.strip().lower() == "all"
             else [s.strip() for s in args.steps.split(",") if s.strip()])
    bad = [s for s in steps if s not in ALL_STEPS]
    if bad:
        _log.error("未知步骤: %s (允许: %s)", bad, ALL_STEPS)
        return 1

    result = run_pipeline(
        db_path=Path(args.db),
        raw_dir=Path(args.raw_dir),
        steps=steps,
        dry_run=args.dry_run,
        skip_ifind=args.skip_ifind,
    )

    _log.info("\n%s", result.summary())
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
