"""scripts/etl_pipeline.py 的单元测试."""
from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

import pytest

# etl_pipeline is in scripts/, need to import carefully
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from etl_pipeline import (
    run_pipeline,
    PipelineResult,
    StepResult,
    ALL_STEPS,
    _step_verify,
    _step_quality,
    _step_fix,
)
from data.schema import init_database


@pytest.fixture
def mini_db(tmp_path):
    """创建一个最小的测试 DB 带一些行."""
    db_path = tmp_path / "test.db"
    init_database(str(db_path))
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        INSERT INTO ipo_master (
            ipo_id, stock_code, company_name_zh, listing_date, listing_chapter,
            offer_price_hkd, offering_size_hkd, intl_oversub, status
        ) VALUES (
            'HK_09999_2024', '09999.HK', '测试公司', '2024-01-15', 'main_board',
            100.0, 5e9, 5.0, 'listed'
        )
    """)
    conn.execute("""
        INSERT INTO ipo_master (
            ipo_id, stock_code, listing_date, listing_chapter,
            offer_price_hkd, offer_price_high, offer_price_low,
            pe_at_offer, status
        ) VALUES (
            'HK_01234_2024', '01234.HK', '2024-03-01', 'main_board',
            50.0, 60.0, 40.0, 250.0, 'listed'
        )
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def mini_csv(tmp_path):
    """创建最小 CSV 文件供 load step 使用."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()

    # 空的 ipo_info CSV (header only)
    ipo_csv = raw_dir / "ifind_ipo_info.csv"
    ipo_csv.write_text("p05310_f001,p05310_f002\n", encoding="utf-8")

    # 空的 cornerstones CSV
    cs_csv = raw_dir / "ifind_cornerstones.csv"
    cs_csv.write_text("p05309_f001,p05309_f002\n", encoding="utf-8")

    return raw_dir


class TestStepVerify:
    def test_ok_on_valid_db(self, mini_db):
        result = _step_verify(mini_db)
        assert result.status in ("ok", "warn")
        assert result.name == "verify"

    def test_skip_on_missing_db(self, tmp_path):
        result = _step_verify(tmp_path / "nonexistent.db")
        assert result.status == "skip"


class TestStepFix:
    def test_pe_outlier_fixed(self, mini_db):
        result = _step_fix(mini_db, dry_run=False)
        assert result.status == "ok"

        conn = sqlite3.connect(str(mini_db))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT pe_at_offer FROM ipo_master WHERE ipo_id='HK_01234_2024'"
        ).fetchone()
        conn.close()
        # pe_at_offer=250 > 200 → should be NULL
        assert row["pe_at_offer"] is None

    def test_pricing_in_range_computed(self, mini_db):
        result = _step_fix(mini_db, dry_run=False)
        conn = sqlite3.connect(str(mini_db))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT pricing_in_range FROM ipo_master WHERE ipo_id='HK_01234_2024'"
        ).fetchone()
        conn.close()
        # (50 - 40) / (60 - 40) = 0.5
        assert row["pricing_in_range"] == 0.5

    def test_dry_run_no_changes(self, mini_db):
        result = _step_fix(mini_db, dry_run=True)
        assert result.status == "ok"

        conn = sqlite3.connect(str(mini_db))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT pe_at_offer FROM ipo_master WHERE ipo_id='HK_01234_2024'"
        ).fetchone()
        conn.close()
        # dry_run → pe_at_offer should remain 250
        assert row["pe_at_offer"] == 250.0


class TestStepQuality:
    def test_scores_refreshed(self, mini_db):
        result = _step_quality(mini_db, dry_run=False)
        assert result.status == "ok"
        assert "avg=" in result.message

    def test_skip_on_missing_db(self, tmp_path):
        result = _step_quality(tmp_path / "nope.db", dry_run=False)
        assert result.status == "skip"


class TestPipelineResult:
    def test_ok_when_all_ok(self):
        pr = PipelineResult(steps=[
            StepResult(name="a", status="ok"),
            StepResult(name="b", status="skip"),
        ])
        assert pr.ok is True

    def test_not_ok_when_fail(self):
        pr = PipelineResult(steps=[
            StepResult(name="a", status="ok"),
            StepResult(name="b", status="fail"),
        ])
        assert pr.ok is False

    def test_summary_contains_all_steps(self):
        pr = PipelineResult(steps=[
            StepResult(name="load", status="ok", message="done", elapsed_s=1.5),
            StepResult(name="fix", status="warn", message="1 issue", elapsed_s=0.3),
        ])
        summary = pr.summary()
        assert "load" in summary
        assert "fix" in summary
        assert "SUCCESS" not in summary or "FAILED" not in summary


class TestRunPipeline:
    def test_verify_and_quality_only(self, mini_db):
        result = run_pipeline(
            db_path=mini_db,
            steps=["verify", "quality"],
            dry_run=False,
        )
        assert len(result.steps) == len(ALL_STEPS)
        # verify and quality should be ok, others skipped
        for s in result.steps:
            if s.name in ("verify", "quality"):
                assert s.status in ("ok", "warn")
            else:
                assert s.status == "skip"

    def test_skip_ifind_skips_cache(self, mini_db):
        result = run_pipeline(
            db_path=mini_db,
            steps=["cache"],
            skip_ifind=True,
        )
        cache_step = [s for s in result.steps if s.name == "cache"][0]
        assert cache_step.status == "skip"
