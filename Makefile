# HK IPO Cornerstone Agent — Makefile
# All commands per PROJECT_SPEC.md §3.1.
#
# Requires uv: https://docs.astral.sh/uv/
#
# Usage examples:
#   make install         # uv sync + install dev extras
#   make lint            # ruff check
#   make format          # ruff format
#   make typecheck       # mypy
#   make test            # pytest (unit only)
#   make test-all        # all tests including integration/e2e (needs docker)
#   make db-up           # docker compose up postgres + qdrant + redis
#   make db-down         # docker compose down
#   make migrate         # alembic upgrade head
#   make analyze IPO=2228.HK
#   make backtest

.DEFAULT_GOAL := help
SHELL := /bin/bash

PYTHON ?= python
UV ?= uv
DOCKER_COMPOSE ?= docker compose

.PHONY: help install lock sync lint format typecheck test test-integration test-e2e test-all \
        db-up db-down db-logs db-psql migrate migrate-new analyze backtest serve clean nuke

help:
	@echo "HK IPO Cornerstone Agent — common commands"
	@echo ""
	@echo "  make install        Install all deps via uv (incl. dev group)"
	@echo "  make lock           Lock dependencies (uv lock)"
	@echo "  make lint           Run ruff check on src/hk_ipo_agent + tests"
	@echo "  make format         Run ruff format"
	@echo "  make typecheck      Run mypy"
	@echo "  make test           Run unit tests"
	@echo "  make test-all       Run unit + integration + e2e tests"
	@echo "  make db-up          docker compose up -d postgres qdrant redis"
	@echo "  make db-down        docker compose down"
	@echo "  make migrate        alembic upgrade head"
	@echo "  make analyze IPO=2228.HK"
	@echo "  make backtest"
	@echo "  make serve          Run FastAPI dev server"

# ---------------- Install / Lock ----------------
# Default install = core deps + dev tooling. Heavy extras are opt-in:
#   make install-parse           # LlamaParse
#   make install-embeddings      # sentence-transformers + torch (~2 GB)
#   make install-reports         # weasyprint + python-docx + matplotlib + plotly
#   make install-airflow         # Apache Airflow (production scheduler)
#   make install-all             # everything (slow, large)
install:
	$(UV) sync

install-parse:
	$(UV) sync --extra parse

install-embeddings:
	$(UV) sync --extra embeddings-local --extra embeddings-cloud

install-reports:
	$(UV) sync --extra reports

install-airflow:
	$(UV) sync --extra scheduler-prod

install-all:
	$(UV) sync --all-extras

lock:
	$(UV) lock

sync:
	$(UV) sync

# ---------------- Lint / Format / Type ----------------
lint:
	$(UV) run ruff check src/hk_ipo_agent tests scripts

format:
	$(UV) run ruff format src/hk_ipo_agent tests scripts

typecheck:
	$(UV) run mypy src/hk_ipo_agent

# ---------------- Tests ----------------
test:
	$(UV) run pytest tests/unit -v

test-integration:
	$(UV) run pytest tests/integration -v -m integration

test-e2e:
	$(UV) run pytest tests/e2e -v -m e2e

test-all:
	$(UV) run pytest tests -v

# ---------------- Docker Compose ----------------
db-up:
	$(DOCKER_COMPOSE) up -d postgres qdrant redis

db-down:
	$(DOCKER_COMPOSE) down

db-logs:
	$(DOCKER_COMPOSE) logs -f postgres qdrant redis

db-psql:
	$(DOCKER_COMPOSE) exec postgres psql -U hkipo -d hkipo

# ---------------- Database migrations ----------------
migrate:
	$(UV) run alembic -c src/hk_ipo_agent/data/migrations/alembic.ini upgrade head

migrate-new:
	@if [ -z "$(MSG)" ]; then echo "Usage: make migrate-new MSG='migration message'"; exit 1; fi
	$(UV) run alembic -c src/hk_ipo_agent/data/migrations/alembic.ini revision --autogenerate -m "$(MSG)"

# ---------------- Analysis ----------------
analyze:
	@if [ -z "$(IPO)" ]; then echo "Usage: make analyze IPO=2228.HK"; exit 1; fi
	$(UV) run python scripts/run_analysis.py --ipo $(IPO)

backtest:
	$(UV) run python scripts/run_backtest.py

serve:
	$(UV) run uvicorn hk_ipo_agent.api.main:app --reload --host 0.0.0.0 --port 8000

# ---------------- Cleanup ----------------
clean:
	rm -rf .ruff_cache .mypy_cache .pytest_cache build dist *.egg-info

nuke: clean
	rm -rf .venv outputs/ daily/
