"""
pytest fixtures for NACS test suite

约定:
    - 所有 DB 测试用 :memory: 或 tmp_path, 永不动 data/nacs_real.db
    - sample IPO/cornerstone fixture 来自 check_health.py 的 T1 baseline
    - 测试导入路径: 把 src/ 加到 sys.path (与 check_health.py 一致)
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

# 让 src/ 包可被导入
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SRC = _PROJECT_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


# =============================================================================
# 路径
# =============================================================================

@pytest.fixture(scope="session")
def project_root() -> Path:
    return _PROJECT_ROOT


@pytest.fixture(scope="session")
def raw_dir(project_root: Path) -> Path:
    return project_root / "data" / "raw" / "ifind"


@pytest.fixture(scope="session")
def configs_dir(project_root: Path) -> Path:
    return project_root / "configs"


# =============================================================================
# 临时 SQLite (function 作用域, 每测试独立, 自动清理)
# =============================================================================

@pytest.fixture
def empty_db(tmp_path):
    """空 DB + schema 已建; 返回 sqlite path"""
    from data.schema import init_database
    db = tmp_path / "test.db"
    init_database(str(db))
    return db


# =============================================================================
# Sample 业务对象 (复用 check_health.py 的 T1 baseline)
# =============================================================================

@pytest.fixture
def sample_cornerstones():
    from nacs_model import CornerstoneInvestor, CornerstoneType
    return [
        CornerstoneInvestor(
            name=f"CS_{i}", ticket_size_hkd=1.5e8,
            type=CornerstoneType.SOVEREIGN_PENSION, aum_usd=10e9,
            hk_ipo_count_5y=20, hk_ipo_avg_m6_return=0.15,
            hk_ipo_winrate_m6=0.7, lockup_discipline_score=0.85,
            sector_expertise=3,
        )
        for i in range(5)
    ]


@pytest.fixture
def make_ipo(sample_cornerstones):
    """返回一个工厂函数, 可指定 regime/cluster 覆写"""
    from nacs_model import (
        IPOOffering, ListingChapter, CompanyType, SponsorTier,
        OfferingStructure, SponsorInfo, MarketEnvironment, LockupContext,
        ProfitableFundamentals,
    )

    def _make(*, regime=None, cluster=0):
        return IPOOffering(
            company_name="Test", stock_code="0001.HK",
            listing_chapter=ListingChapter.MAIN_BOARD_PROFITABLE,
            company_type=CompanyType.PROFITABLE, cornerstones=sample_cornerstones,
            offering=OfferingStructure(
                pricing_in_range=0.7, intl_oversubscription=10.0,
                public_oversubscription=30.0, clawback_triggered=True,
                greenshoe_pct=0.15, offering_size_hkd=1.5e9,
                pe_at_offer=15, pe_peer_median=22, last_round_premium=-0.10),
            sponsor=SponsorInfo(primary_sponsor="UBS",
                                primary_tier=SponsorTier.TIER_1, joint_sponsor_count=1),
            market=MarketEnvironment(
                hsi_60d_return=0.03, hsi_60d_vol_annualized=0.20,
                hsi_60d_vol_pct_rank=0.5, hsi_valuation_pct=0.5,
                hk_ipo_30d_avg_d30=0.05, hk_ipo_30d_breakage_rate=0.50,
                southbound_30d_net_normalized=0.0, sector_60d_vol_annualized=0.30),
            lockup=LockupContext(
                lockup_months=6, overhang_ratio=1.0,
                fundamental_risk_score=0.30, peer_lockup_avg_drawdown=0.10,
                pe_vs_history_pct=0.50),
            profitable=ProfitableFundamentals(
                revenue_cagr_3y=0.30, gross_margin_trend=0.05,
                roe_avg_3y=0.20, net_debt_to_ebitda=1.0, fcf_positive_years=3),
            regime_score=regime, cluster_cornerstone_count=cluster,
        )

    return _make
