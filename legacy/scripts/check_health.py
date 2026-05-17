"""
数据 & 模型健康检查 - 一键验证

用法:
    python check_health.py
    python check_health.py --db data/nacs_real.db
"""
import argparse
import sys
import sqlite3
from datetime import date
from pathlib import Path

# Windows 控制台默认 GBK，强制 UTF-8 以支持 ✓/❌ 等字符
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(ROOT / 'src'))


def check_db(db_path):
    print(f"\n[1/3] 数据库健康检查: {db_path}")
    print('-' * 60)
    if not db_path.exists():
        print(f"  ❌ 文件不存在")
        return False

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # 表清单 + 行数
    expected = {
        'ipo_master': 380, 'ipo_cornerstone_link': 1500,
        'cornerstone_master': 1300, 'ipo_financials': 1500,
        'ipo_returns': 350, 'cornerstone_aliases': 1000,
        'cornerstone_performance_asof': 30000,  # v8: 缓存表已预填充
    }
    tables = [r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
    ok = True
    for t in tables:
        if t.startswith('sqlite'):
            continue
        n = cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        exp = expected.get(t)
        flag = '✓' if (exp is None or n >= exp) else '⚠'
        print(f"  {flag} {t}: {n} 行" + (f" (期望 ≥{exp})" if exp else ""))
        if exp and n < exp:
            ok = False

    # 数据完整性抽查
    print("\n  数据完整性:")
    n_no_chapter = cur.execute(
        "SELECT COUNT(*) FROM ipo_master WHERE listing_chapter IS NULL").fetchone()[0]
    n_no_cs = cur.execute("""
        SELECT COUNT(*) FROM ipo_master m
        WHERE NOT EXISTS (SELECT 1 FROM ipo_cornerstone_link l WHERE l.ipo_id = m.ipo_id)
    """).fetchone()[0]
    n_no_returns = cur.execute("""
        SELECT COUNT(*) FROM ipo_master m
        WHERE NOT EXISTS (SELECT 1 FROM ipo_returns r WHERE r.ipo_id = m.ipo_id)
    """).fetchone()[0]
    n_aff = cur.execute(
        "SELECT COUNT(*) FROM ipo_cornerstone_link WHERE affiliation_flag IN (1,2)").fetchone()[0]
    print(f"  · 无章节信息 IPO: {n_no_chapter}")
    print(f"  · 无基石数据 IPO: {n_no_cs}")
    print(f"  · 无收益数据 IPO: {n_no_returns}")
    print(f"  · affiliation 标记基石: {n_aff} (含强关联+簇基石)")

    conn.close()
    return ok


def check_model_imports():
    print("\n[2/3] 模型代码导入检查")
    print('-' * 60)
    try:
        from nacs_model import (
            compute_nacs, compute_regime_score, IPOOffering,
            REGIME_GATE_THRESHOLD, CLUSTER_BONUS_TABLE,
        )
        print(f"  ✓ nacs_model 导入成功")
        print(f"  ✓ REGIME_GATE_THRESHOLD = {REGIME_GATE_THRESHOLD}")
        print(f"  ✓ CLUSTER_BONUS_TABLE = {CLUSTER_BONUS_TABLE}")
        from data.dao import compute_cornerstone_perf_asof
        print(f"  ✓ data.dao 导入成功")
        return True
    except Exception as e:
        print(f"  ❌ 导入失败: {e}")
        return False


def check_model_functional():
    print("\n[3/3] 模型功能性测试")
    print('-' * 60)
    try:
        from nacs_model import (
            IPOOffering, ListingChapter, CompanyType, SponsorTier, compute_nacs,
            OfferingStructure, SponsorInfo, MarketEnvironment, LockupContext,
            ProfitableFundamentals, CornerstoneInvestor, CornerstoneType,
            compute_regime_score,
        )

        cs = [CornerstoneInvestor(
            name=f"CS_{i}", ticket_size_hkd=1.5e8,
            type=CornerstoneType.SOVEREIGN_PENSION, aum_usd=10e9,
            hk_ipo_count_5y=20, hk_ipo_avg_m6_return=0.15,
            hk_ipo_winrate_m6=0.7, lockup_discipline_score=0.85,
            sector_expertise=3,
        ) for i in range(5)]

        def make(regime=None, cluster=0):
            return IPOOffering(
                company_name="Test", stock_code="0001.HK",
                listing_chapter=ListingChapter.MAIN_BOARD_PROFITABLE,
                company_type=CompanyType.PROFITABLE, cornerstones=cs,
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

        # T1 baseline
        r1 = compute_nacs(make())
        assert r1.decision != "SKIP", f"baseline 应非 SKIP, 实际 {r1.decision}"
        print(f"  ✓ T1 baseline: NACS={r1.nacs_adjusted:.3f}, decision={r1.decision}")

        # T2 regime gate 阻断
        r2 = compute_nacs(make(regime=-0.05))
        assert r2.decision == "SKIP", f"regime<0 应 SKIP, 实际 {r2.decision}"
        print(f"  ✓ T2 regime=-0.05: 强制 SKIP (原 {r1.decision})")

        # T3 cluster bonus
        r3 = compute_nacs(make(cluster=3))
        assert r3.Q_ecosystem >= r1.Q_ecosystem, "cluster bonus 应提升 Q_e"
        print(f"  ✓ T3 cluster=3: Q_e={r3.Q_ecosystem:.3f} > {r1.Q_ecosystem:.3f}")

        # T4 regime helper
        hist = [
            (date(2025,1,1), 0.1), (date(2025,2,1), 0.05), (date(2025,2,15), -0.02),
            (date(2025,3,1), 0.08), (date(2025,3,15), 0.06), (date(2025,4,1), 0.04),
            (date(2025,4,15), 0.09), (date(2025,5,1), 0.11), (date(2025,5,15), 0.07),
            (date(2025,5,25), 0.03),
        ]
        score = compute_regime_score(hist, date(2025, 7, 1))
        assert score is not None, "应返回有效 score"
        print(f"  ✓ T4 compute_regime_score: {score:+.4f}")

        # ★ T5 v8: RELATIONSHIP 决策的仓位应为 0
        from nacs_model import _position_from_nacs
        pos_rel, dec_rel = _position_from_nacs(0.30)
        assert dec_rel == "RELATIONSHIP" and pos_rel == 0.0, \
            f"v8: RELATIONSHIP 仓位应为 0, 实际 {pos_rel} ({dec_rel})"
        print(f"  ✓ T5 v8 RELATIONSHIP 仓位归零: NACS=0.30 → ({pos_rel}, '{dec_rel}')")

        return True
    except Exception as e:
        print(f"  ❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', default=str(ROOT / 'data' / 'nacs_real.db'))
    args = parser.parse_args()

    print("="*60)
    print("NACS v7 健康检查")
    print("="*60)

    db_ok = check_db(Path(args.db))
    imp_ok = check_model_imports()
    func_ok = check_model_functional()

    print("\n" + "="*60)
    if db_ok and imp_ok and func_ok:
        print("✅ 全部通过 - 可以运行 python run_v7_backtest.py")
        sys.exit(0)
    else:
        print("⚠ 有问题需要修复, 详见上面输出")
        sys.exit(1)


if __name__ == "__main__":
    main()
