"""
最小烟雾测试: 模块导入 + schema 初始化 + DB 抽样

用于快速发现 import path / 环境配置问题, 通常 < 1s
"""
from __future__ import annotations


def test_import_nacs_model():
    import nacs_model  # noqa: F401
    from nacs_model import (  # noqa: F401
        compute_nacs, compute_regime_score, IPOOffering,
        REGIME_GATE_THRESHOLD, CLUSTER_BONUS_TABLE, CornerstoneType,
    )


def test_import_dao():
    from data import dao  # noqa: F401
    from data.dao import (  # noqa: F401
        db_connect, upsert_cornerstone, add_alias, upsert_ipo,
        link_cornerstone_to_ipo, resolve_cornerstone_id,
        compute_cornerstone_perf_asof,
    )


def test_import_config():
    from config import NacsConfig, get_config, set_config, reset_config  # noqa: F401


def test_import_etl():
    from data_sources.ifind import load_to_db, field_mappings  # noqa: F401


def test_schema_init_creates_all_tables(empty_db):
    import sqlite3
    expected = {
        "cornerstone_master", "cornerstone_aliases", "ipo_master",
        "ipo_cornerstone_link", "price_history", "cornerstone_performance_asof",
        "ipo_returns", "sponsor_performance_asof", "db_metadata",
        "market_environment_cache",
    }
    with sqlite3.connect(str(empty_db)) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    actual = {r[0] for r in rows}
    missing = expected - actual
    assert not missing, f"缺表: {missing}"


def test_schema_version_metadata(empty_db):
    import sqlite3
    with sqlite3.connect(str(empty_db)) as conn:
        v = conn.execute(
            "SELECT value FROM db_metadata WHERE key = 'schema_version'"
        ).fetchone()
    assert v is not None
    assert v[0] == "1.0"
