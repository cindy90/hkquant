"""src/log.py 的单元测试."""
from __future__ import annotations

import logging
import os

import pytest

from log import get_logger, setup_cli_logging, _ROOT_NAME


@pytest.fixture(autouse=True)
def _reset_logging():
    """每个测试后重置 nacs root logger, 避免状态泄漏."""
    import log as log_mod
    log_mod._CONFIGURED = False
    root = logging.getLogger(_ROOT_NAME)
    root.handlers.clear()
    root.setLevel(logging.WARNING)
    yield
    log_mod._CONFIGURED = False
    root.handlers.clear()
    root.setLevel(logging.WARNING)


class TestGetLogger:
    def test_returns_prefixed_logger(self):
        logger = get_logger("foo.bar")
        assert logger.name == f"{_ROOT_NAME}.foo.bar"

    def test_already_prefixed_not_doubled(self):
        logger = get_logger(f"{_ROOT_NAME}.sub")
        assert logger.name == f"{_ROOT_NAME}.sub"

    def test_default_level_is_warning(self):
        get_logger("x")
        root = logging.getLogger(_ROOT_NAME)
        assert root.level == logging.WARNING

    def test_handler_attached_to_root(self):
        get_logger("y")
        root = logging.getLogger(_ROOT_NAME)
        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0], logging.StreamHandler)


class TestSetupCliLogging:
    def test_lowers_to_info(self):
        get_logger("cli_test")
        setup_cli_logging()
        root = logging.getLogger(_ROOT_NAME)
        assert root.level == logging.INFO

    def test_custom_level(self):
        get_logger("cli_test2")
        setup_cli_logging("DEBUG")
        root = logging.getLogger(_ROOT_NAME)
        assert root.level == logging.DEBUG

    def test_formatter_has_timestamp(self):
        get_logger("cli_test3")
        setup_cli_logging()
        root = logging.getLogger(_ROOT_NAME)
        fmt = root.handlers[0].formatter
        assert fmt is not None
        assert "asctime" in fmt._fmt


class TestEnvOverride:
    def test_env_var_overrides_default(self, monkeypatch):
        import log as log_mod
        log_mod._CONFIGURED = False
        monkeypatch.setenv("NACS_LOG_LEVEL", "DEBUG")
        get_logger("env_test")
        root = logging.getLogger(_ROOT_NAME)
        assert root.level == logging.DEBUG
