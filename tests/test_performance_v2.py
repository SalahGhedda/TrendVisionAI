from __future__ import annotations

from trendvision_ai.live_pipeline import LivePipelineStore
from trendvision_ai.performance_v2 import install_performance_patches
from trendvision_ai.trade_alert_journal import TradeAlertJournalStore
from trendvision_ai.trade_plan_stats import TradePlanStatsEngine
from trendvision_ai.trade_plans import TradePlanResult, TradePlanStore


install_performance_patches()


def _plan(ticker: str, decision: str) -> TradePlanResult:
    actionable = decision == "POTENTIAL TRADE"
    return TradePlanResult(
        ticker=ticker,
        model="test-model",
        decision=decision,
        confidence="MEDIUM",
        risk_level="MODERATE",
        chart_structure="MODERATE",
        setup_type="Test Setup",
        summary="test",
        entry_low=10.0 if actionable else None,
        entry_high=10.1 if actionable else None,
        stop_loss=9.8 if actionable else None,
        target_1=10.4 if actionable else None,
        target_2=10.8 if actionable else None,
        risk_reward_target_1=1.0 if actionable else None,
        risk_reward_target_2=2.0 if actionable else None,
        entry_trigger="test",
        invalidation="test",
        positive_factors=[],
        risk_factors=[],
        chart_observations=[],
        what_to_confirm=[],
        created_at="2026-08-11T10:00:00-04:00",
        plan_version=3,
    )


def test_refresh_evaluations_skips_non_actionable_plans(tmp_path) -> None:
    store = TradePlanStore(tmp_path / "trendvision.db")
    watch_id = store.save(_plan("WATCH", "WATCH"), {}, "watch.png")
    potential_id = store.save(_plan("LIVE", "POTENTIAL TRADE"), {}, "live.png")

    called: list[int] = []

    def fake_evaluate(plan_id: int, *, horizon_minutes: int = 240):
        called.append(int(plan_id))
        return {"status": "WAITING FOR ENTRY"}

    store.evaluate = fake_evaluate  # type: ignore[method-assign]
    store.refresh_evaluations(limit=100)

    assert potential_id in called
    assert watch_id not in called


def test_stats_overview_and_patterns_share_one_cached_row_scan(tmp_path) -> None:
    engine = TradePlanStatsEngine(tmp_path / "trendvision.db")
    calls = 0

    def fake_rows(limit: int = 1000):
        nonlocal calls
        calls += 1
        return []

    engine._rows = fake_rows  # type: ignore[method-assign]

    assert engine.overview()["total_plans"] == 0
    assert engine.pattern_stats(min_resolved=0) == []
    assert calls == 1


def test_trade_journal_sync_reads_only_final_trade_alerts(tmp_path) -> None:
    database = tmp_path / "trendvision.db"
    live = LivePipelineStore(database)
    journal = TradeAlertJournalStore(database)

    live.record_once(
        dedup_key="scan:1",
        ticker="ABCD",
        stage="STRATEGY_MATCH",
        status="KNOWN SETUP RECOGNIZED",
        payload={"strategy_name": "Breakout"},
    )
    live.record_once(
        dedup_key="trade:1",
        ticker="ABCD",
        stage="FINAL_TRADE_ALERT",
        status="READY FOR MANUAL DECISION",
        session_id=7,
        plan_id=9,
        payload={
            "strategy_id": "HOD_BREAKOUT",
            "strategy_name": "High-of-Day Breakout",
            "entry_low": 2.10,
            "entry_high": 2.15,
            "stop_loss": 2.00,
            "target_1": 2.30,
            "target_2": 2.50,
            "risk_reward_target_1": 1.0,
            "risk_reward_target_2": 2.0,
        },
    )

    assert journal.sync_from_live_events(live) == 1
    rows = journal.list_alerts()
    assert len(rows) == 1
    assert rows[0]["ticker"] == "ABCD"
    assert rows[0]["manual_result"] == "OPEN"
