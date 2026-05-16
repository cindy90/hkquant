"""Cross-platform dev task runner — `make`-equivalent for Windows users.

GNU make is not bundled with Windows. This script mirrors the most common
Makefile targets so `uv run python scripts/dev.py <target>` works identically
on Windows / macOS / Linux.

Usage:
    uv run python scripts/dev.py install
    uv run python scripts/dev.py lint
    uv run python scripts/dev.py format
    uv run python scripts/dev.py typecheck
    uv run python scripts/dev.py test
    uv run python scripts/dev.py db-up
    uv run python scripts/dev.py db-down
    uv run python scripts/dev.py migrate
    uv run python scripts/dev.py migrate-new "message"
    uv run python scripts/dev.py serve
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ALEMBIC_CONFIG = "src/hk_ipo_agent/data/migrations/alembic.ini"


def _run(cmd: list[str], *, env: dict[str, str] | None = None) -> int:
    """Run a subprocess command, streaming stdout/stderr; return its exit code."""
    full_env = {**os.environ, **(env or {})}
    print(f"$ {' '.join(cmd)}", file=sys.stderr, flush=True)
    return subprocess.call(cmd, cwd=str(REPO_ROOT), env=full_env)


def _uv(*args: str, env: dict[str, str] | None = None) -> int:
    return _run(["uv", *args], env=env)


# ----------------------------------------------------------------------------
# Targets (mirrors Makefile)
# ----------------------------------------------------------------------------


def install() -> int:
    return _uv("sync")


def install_all() -> int:
    return _uv("sync", "--all-extras")


def lock() -> int:
    return _uv("lock")


def lint() -> int:
    return _uv("run", "ruff", "check", "src/hk_ipo_agent", "tests", "scripts")


def format_() -> int:
    return _uv("run", "ruff", "format", "src/hk_ipo_agent", "tests", "scripts")


def typecheck() -> int:
    return _uv("run", "mypy", "src/hk_ipo_agent")


def test() -> int:
    return _uv("run", "pytest", "tests/unit", "-v")


def test_all() -> int:
    return _uv("run", "pytest", "tests", "-v")


def db_up() -> int:
    return _run(["docker", "compose", "up", "-d", "postgres", "qdrant", "redis"])


def db_down() -> int:
    return _run(["docker", "compose", "down"])


def migrate() -> int:
    # PYTHONUTF8=1 is required on Windows for alembic.ini parsing in repos
    # with non-ASCII characters in the path.
    return _uv("run", "alembic", "-c", ALEMBIC_CONFIG, "upgrade", "head", env={"PYTHONUTF8": "1"})


def migrate_new(message: str) -> int:
    return _uv(
        "run",
        "alembic",
        "-c",
        ALEMBIC_CONFIG,
        "revision",
        "--autogenerate",
        "-m",
        message,
        env={"PYTHONUTF8": "1"},
    )


def serve() -> int:
    return _uv(
        "run",
        "uvicorn",
        "hk_ipo_agent.api.main:app",
        "--reload",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
    )


TARGETS: dict[str, object] = {
    "install": install,
    "install-all": install_all,
    "lock": lock,
    "lint": lint,
    "format": format_,
    "typecheck": typecheck,
    "test": test,
    "test-all": test_all,
    "db-up": db_up,
    "db-down": db_down,
    "migrate": migrate,
    "migrate-new": migrate_new,
    "serve": serve,
}


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in {"-h", "--help", "help"}:
        print("Available targets:")
        for name in TARGETS:
            print(f"  {name}")
        return 0

    target = sys.argv[1]
    fn = TARGETS.get(target)
    if fn is None:
        print(f"Unknown target: {target}", file=sys.stderr)
        print("Run `python scripts/dev.py help` to see available targets.", file=sys.stderr)
        return 2

    args = sys.argv[2:]
    return int(fn(*args) or 0)  # type: ignore[operator]


if __name__ == "__main__":
    sys.exit(main())
