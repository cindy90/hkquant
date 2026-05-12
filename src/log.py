"""
NACS 项目统一日志配置

用法:
    from log import get_logger
    logger = get_logger(__name__)
    logger.info("加载完成, %d 行", n)
    logger.warning("字段缺失: %s", field)

默认行为:
    - 库代码: WARNING 及以上输出到 stderr (不影响 stdout 管道)
    - CLI 脚本: 调用 setup_cli_logging() 后降为 INFO, 格式带时间戳

环境变量:
    NACS_LOG_LEVEL: 覆盖默认日志级别 (DEBUG / INFO / WARNING / ERROR)
"""
from __future__ import annotations

import logging
import os
import sys

_CONFIGURED = False

# 全局根 logger 名称前缀
_ROOT_NAME = "nacs"


def get_logger(name: str) -> logging.Logger:
    """获取以 nacs. 为前缀的 logger, 首次调用时配置默认 handler."""
    _ensure_configured()
    qualified = f"{_ROOT_NAME}.{name}" if not name.startswith(_ROOT_NAME) else name
    return logging.getLogger(qualified)


def setup_cli_logging(level: int | str | None = None) -> None:
    """CLI 入口调用: 把 nacs 根 logger 降为 INFO, 格式含时间戳."""
    root = logging.getLogger(_ROOT_NAME)
    env_level = os.environ.get("NACS_LOG_LEVEL", "").upper()
    effective = level or env_level or "INFO"
    root.setLevel(effective)

    # 替换已有 handler 的 formatter
    for h in root.handlers:
        h.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        ))


def _ensure_configured() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    root = logging.getLogger(_ROOT_NAME)
    env_level = os.environ.get("NACS_LOG_LEVEL", "").upper()
    root.setLevel(env_level or "WARNING")

    if not root.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(
            "[%(levelname)s] %(name)s: %(message)s"
        ))
        root.addHandler(handler)
