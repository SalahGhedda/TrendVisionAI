from pathlib import Path

from trendvision_ai.live_pipeline import LivePipelineStore
from trendvision_ai.trade_alert_journal import TradeAlertJournalStore


def _record_final_alert(live: LivePipelineStore, *, ticker: str = "TEST") -> int:
    created, event = live.record_once(
        dedup_key=f"final:{ticker}:1",
        ticker=ticker,
        session_id=1,
        stage="FINAL_TRADE_ALERT",
        status="READY FOR MANUAL DECISION",
        plan_id=7,
        payload={
            "strategy_id": "BREAKOUT_RETEST",
            "strategy_name": "Breakout + Retest",
            "entry_low": 1.20,
            "entry_high": 1.25,
            "stop_loss": 1.10,
            "target_1": 1.40,
            "target_2": 1.60,
            "risk_reward_target_1": 1.5,
            "risk_reward_target_2": 3.5,
        },
    )
    assert created is True
    assert event is not None
    return int(event["id"])


def test_syncs_final_trade_alert_into_persistent_journal(tmp_path: Path):
    database = tmp_path / "journal.db"
    live = LivePipelineStore(database)
    event_id = _record_final_alert(live)

    journal = TradeAlertJournalStore(database)
    journal.sync_from_live_events(live)
    alerts = journal.list_alerts()

    assert len(alerts) == 1
    alert = alerts[0]
    assert alert["live_event_id"] == event_id
    assert alert["ticker"] == "TEST"
    assert alert["strategy_id"] == "BREAKOUT_RETEST"
    assert alert["entry_low"] == 1.20
    assert alert["entry_high"] == 1.25
    assert alert["stop_loss"] == 1.10
    assert alert["target_1"] == 1.40
    assert alert["target_2"] == 1.60
    assert alert["manual_result"] == "OPEN"


def test_sync_is_idempotent_and_preserves_manual_result(tmp_path: Path):
    database = tmp_path / "journal-idempotent.db"
    live = LivePipelineStore(database)
    _record_final_alert(live)

    journal = TradeAlertJournalStore(database)
    journal.sync_from_live_events(live)
    alert_id = int(journal.list_alerts()[0]["id"])
    journal.set_manual_result(alert_id, "WIN")

    journal.sync_from_live_events(live)
    alerts = journal.list_alerts()
    assert len(alerts) == 1
    assert alerts[0]["manual_result"] == "WIN"


def test_manual_win_loss_stats(tmp_path: Path):
    database = tmp_path / "journal-stats.db"
    live = LivePipelineStore(database)
    _record_final_alert(live, ticker="AAA")
    created, _ = live.record_once(
        dedup_key="final:BBB:2",
        ticker="BBB",
        session_id=2,
        stage="FINAL_TRADE_ALERT",
        status="READY FOR MANUAL DECISION",
        plan_id=8,
        payload={
            "strategy_id": "HOD_BREAKOUT",
            "strategy_name": "High-of-Day Breakout",
            "entry_low": 2.0,
            "entry_high": 2.1,
            "stop_loss": 1.8,
            "target_1": 2.4,
            "target_2": 2.8,
        },
    )
    assert created is True

    journal = TradeAlertJournalStore(database)
    journal.sync_from_live_events(live)
    alerts = journal.list_alerts()
    assert len(alerts) == 2

    journal.set_manual_result(int(alerts[0]["id"]), "WIN")
    journal.set_manual_result(int(alerts[1]["id"]), "LOSS")
    stats = journal.stats()

    assert stats["total"] == 2
    assert stats["open"] == 0
    assert stats["wins"] == 1
    assert stats["losses"] == 1
    assert stats["manual_win_rate_pct"] == 50.0


def test_reset_trade_to_open(tmp_path: Path):
    database = tmp_path / "journal-reset.db"
    live = LivePipelineStore(database)
    _record_final_alert(live)

    journal = TradeAlertJournalStore(database)
    journal.sync_from_live_events(live)
    alert_id = int(journal.list_alerts()[0]["id"])
    journal.set_manual_result(alert_id, "LOSS")
    reset = journal.set_manual_result(alert_id, "OPEN")

    assert reset is not None
    assert reset["manual_result"] == "OPEN"
    assert reset["result_updated_at"] is None
