"""Unit tests for ``orchestrator.hitl`` helper functions.

Covers ``approve()``, ``reject()``, and ``hitl_enabled()`` per ADR 0010 §4.
"""

from __future__ import annotations

from hk_ipo_agent.common.settings import get_settings
from hk_ipo_agent.orchestrator.hitl import approve, hitl_enabled, reject


class TestHitlEnabled:
    def test_default_is_false(self, monkeypatch) -> None:
        monkeypatch.setenv("HK_IPO__ORCHESTRATOR__ENABLE_HITL", "false")
        get_settings.cache_clear()
        assert hitl_enabled() is False

    def test_enabled_when_env_true(self, monkeypatch) -> None:
        monkeypatch.setenv("HK_IPO__ORCHESTRATOR__ENABLE_HITL", "true")
        get_settings.cache_clear()
        assert hitl_enabled() is True


class TestApprove:
    def test_sets_approved_status(self) -> None:
        state = {"ipo_id": "x", "runtime_meta": {"started_at": "t0"}}
        result = approve(state, reviewer="analyst_a")
        assert result["hitl_status"] == "approved"
        assert result["runtime_meta"]["hitl_reviewer"] == "analyst_a"
        assert "hitl_approved_at" in result["runtime_meta"]
        # Preserves prior meta.
        assert result["runtime_meta"]["started_at"] == "t0"

    def test_works_with_empty_meta(self) -> None:
        state = {"ipo_id": "x"}
        result = approve(state, reviewer="boss")
        assert result["hitl_status"] == "approved"
        assert result["runtime_meta"]["hitl_reviewer"] == "boss"


class TestReject:
    def test_sets_rejected_status_with_reason(self) -> None:
        state = {"ipo_id": "x", "runtime_meta": {}}
        result = reject(state, reviewer="analyst_b", reason="valuation too high")
        assert result["hitl_status"] == "rejected"
        assert result["runtime_meta"]["hitl_reviewer"] == "analyst_b"
        assert result["runtime_meta"]["hitl_reject_reason"] == "valuation too high"
        assert "hitl_rejected_at" in result["runtime_meta"]

    def test_works_with_missing_meta(self) -> None:
        state = {"ipo_id": "y"}
        result = reject(state, reviewer="cio", reason="regime turning")
        assert result["hitl_status"] == "rejected"
        assert result["runtime_meta"]["hitl_reject_reason"] == "regime turning"
