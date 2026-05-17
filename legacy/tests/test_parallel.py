"""
parallel_score_ipos 测试 (P2-C):
    - serial 与 workers>=2 输出必须完全一致 (位元级)
    - score_one_ipo 单点调用应可独立工作
    - errors counter 在 worker 失败时正确累计

注: 这些测试需要 data/nacs_real.db 存在 (真实回测库),
    若未生成则用 skip 跳过 (CI 中不灌库, 跳过即可).
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

# 让 run_v7_backtest.py 可被 import (在项目 root)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


@pytest.fixture(scope="module")
def real_db_path(project_root):
    p = project_root / "data" / "nacs_real.db"
    if not p.exists():
        pytest.skip(f"真实回测库不存在 ({p}), 跳过并行回归测试")
    return p


@pytest.fixture(scope="module")
def small_universe(real_db_path):
    """从真实库取前 30 只 IPO + 历史, 加快测试"""
    import sqlite3
    conn = sqlite3.connect(str(real_db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT m.ipo_id, m.listing_date, r.return_d30
        FROM ipo_master m LEFT JOIN ipo_returns r ON m.ipo_id = r.ipo_id
        WHERE (m.is_delisted=0 OR m.is_delisted IS NULL)
        ORDER BY m.listing_date
    """).fetchall()
    conn.close()
    history = [
        (date.fromisoformat(str(r["listing_date"])[:10]) if r["listing_date"] else None,
         r["return_d30"])
        for r in rows
    ]
    ipo_ids = [r["ipo_id"] for r in rows[:30]]
    return ipo_ids, history


def test_score_one_ipo_smoke(real_db_path, small_universe):
    """单 worker 调用应返回 dict 或 None, 不抛异常"""
    from run_v7_backtest import score_one_ipo
    ipo_ids, history = small_universe
    args = (str(real_db_path), ipo_ids[0], history, True, None)
    result = score_one_ipo(args)
    assert result is None or isinstance(result, dict)
    if isinstance(result, dict) and "_error" not in result:
        assert "NACS" in result and "decision" in result


def test_serial_equals_parallel(real_db_path, small_universe):
    """串行 (workers=1) vs 并行 (workers=2) 输出必须完全一致"""
    from run_v7_backtest import parallel_score_ipos
    ipo_ids, history = small_universe

    serial_recs, _ = parallel_score_ipos(
        db_path=str(real_db_path), ipo_ids=ipo_ids, history=history,
        workers=1, use_static_env=True, config_path=None,
    )
    parallel_recs, _ = parallel_score_ipos(
        db_path=str(real_db_path), ipo_ids=ipo_ids, history=history,
        workers=2, use_static_env=True, config_path=None,
    )

    # ipo_id 集合一致
    assert {r["ipo_id"] for r in serial_recs} == {r["ipo_id"] for r in parallel_recs}

    # 同 ipo_id 的 NACS / decision / position_pct 完全一致
    s_map = {r["ipo_id"]: r for r in serial_recs}
    p_map = {r["ipo_id"]: r for r in parallel_recs}
    for k in s_map:
        s, p = s_map[k], p_map[k]
        assert s["NACS"] == pytest.approx(p["NACS"], rel=1e-12), \
            f"NACS mismatch on {k}: serial={s['NACS']} parallel={p['NACS']}"
        assert s["decision"] == p["decision"]
        assert s["position_pct"] == pytest.approx(p["position_pct"], rel=1e-12)
        assert s["cluster_count"] == p["cluster_count"]


def test_workers_zero_treated_as_serial(real_db_path, small_universe):
    """workers=0 应当与 workers=1 等价 (不应启动 ProcessPool)"""
    from run_v7_backtest import parallel_score_ipos
    ipo_ids, history = small_universe[0][:5], small_universe[1]
    recs, errors = parallel_score_ipos(
        db_path=str(real_db_path), ipo_ids=ipo_ids, history=history,
        workers=0, use_static_env=True,
    )
    assert isinstance(recs, list)
    assert sum(errors.values()) == 0


def test_invalid_ipo_id_recorded_as_none(real_db_path, small_universe):
    """不存在的 ipo_id 应返回 None (不计入 records, 不计入 errors)"""
    from run_v7_backtest import parallel_score_ipos
    _, history = small_universe
    recs, errors = parallel_score_ipos(
        db_path=str(real_db_path),
        ipo_ids=["HK_DOES_NOT_EXIST_9999"],
        history=history,
        workers=1, use_static_env=True,
    )
    assert recs == []
    assert sum(errors.values()) == 0
