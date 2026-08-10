from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from trendvision_ai.auto_chart import normalize_bars_payload
from trendvision_ai.live_pipeline import (
    LivePipelineStore,
    final_trade_alert_gate,
    regular_session_state,
)


NY = ZoneInfo("America/New_York")


def _qualification(status: str = "EXPERIMENTALLY QUALIFIED"):
    return {"status": status, "positive_patterns": [{"pattern": "SIGNAL: BREAKOUT"}]}


def _plan():
    return {
        "decision": "POTENTIAL TRADE",
        "risk_level": "MODERATE",
        "chart_structure": "STRONG",
        "entry_low": 10.0,
        "entry_high": 10.2,
        "stop_loss": 9.5,
        "target_1": 11.0,
        "target_2": 12.0,
    }


def _snapshot(*, quote_fresh: bool = True, spread_pct: float = 2.0):
    return {
        "alpaca_market_context": {
            "current_context_usable": True,
            "market_event_freshness": {"latest_quote_fresh": quote_fresh},
            "latest": {"spread_pct": spread_pct},
        },
        "trendvision": {"recent_convergence": {"events": []}},
    }


def test_regular_session_gate():
    open_time = datetime(2026, 8, 10, 11, 0, tzinfo=NY)
    closed_time = datetime(2026, 8, 10, 18, 0, tzinfo=NY)
    assert regular_session_state(open_time)["open"] is True
    assert regular_session_state(closed_time)["open"] is False


def test_final_alert_gate_allows_coherent_qualified_plan():
    open_time = datetime(2026, 8, 10, 11, 0, tzinfo=NY)
    gate = final_trade_alert_gate(
        qualification=_qualification(),
        plan=_plan(),
        snapshot=_snapshot(),
        now=open_time,
    )
    assert gate["allowed"] is True
    assert gate["blockers"] == []


def test_final_alert_gate_blocks_stale_quote_and_wide_spread():
    open_time = datetime(2026, 8, 10, 11, 0, tzinfo=NY)
    gate = final_trade_alert_gate(
        qualification=_qualification(),
        plan=_plan(),
        snapshot=_snapshot(quote_fresh=False, spread_pct=20.0),
        now=open_time,
    )
    assert gate["allowed"] is False
    assert "NO_FRESH_QUOTE" in gate["blockers"]
    assert "SPREAD_TOO_WIDE" in gate["blockers"]


def test_pipeline_store_deduplicates_events(tmp_path: Path):
    store = LivePipelineStore(tmp_path / "pipeline.db")
    first, event1 = store.record_once(
        dedup_key="qualified:1",
        ticker="TEST",
        session_id=1,
        stage="QUALIFIED_CANDIDATE",
        status="EXPERIMENTALLY QUALIFIED",
    )
    second, event2 = store.record_once(
        dedup_key="qualified:1",
        ticker="TEST",
        session_id=1,
        stage="QUALIFIED_CANDIDATE",
        status="EXPERIMENTALLY QUALIFIED",
    )
    assert first is True
    assert second is False
    assert event1["id"] == event2["id"]


def test_normalize_alpaca_bars_payload():
    bars = normalize_bars_payload(
        {
            "bars": [
                {"t": "2026-08-10T15:00:00Z", "o": 1.0, "h": 1.2, "l": 0.9, "c": 1.1, "v": 1000},
                {"t": "2026-08-10T15:01:00Z", "o": 1.1, "h": 1.3, "l": 1.0, "c": 1.25, "v": 2000},
            ]
        }
    )
    assert len(bars) == 2
    assert bars[1]["close"] == 1.25
    assert bars[1]["volume"] == 2000.0
