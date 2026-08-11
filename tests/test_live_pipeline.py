from __future__ import annotations

import json
import sqlite3
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


def _qualification(status: str = "INSUFFICIENT EVIDENCE"):
    return {"status": status, "positive_patterns": []}


def _strategy_validation(status: str = "CALIBRATION IMMATURE"):
    return {"status": status, "strategy_id": "HOD_BREAKOUT"}


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
        "strategy_context": {
            "recognized": True,
            "primary": {
                "strategy_id": "HOD_BREAKOUT",
                "name": "High-of-Day Breakout",
                "status": "CANDIDATE",
                "instance_key": "HOD_BREAKOUT|20260810T1100|10.0000",
                "key_levels": {"entry_reference": 10.0},
                "plan_constraints": {"max_entry_extension_pct": 4.0},
            },
        },
        "trendvision": {"recent_convergence": {"events": []}},
    }


def test_regular_session_gate():
    open_time = datetime(2026, 8, 10, 11, 0, tzinfo=NY)
    closed_time = datetime(2026, 8, 10, 18, 0, tzinfo=NY)
    assert regular_session_state(open_time)["open"] is True
    assert regular_session_state(closed_time)["open"] is False


def test_final_alert_gate_allows_known_setup_with_immature_history():
    open_time = datetime(2026, 8, 10, 11, 0, tzinfo=NY)
    gate = final_trade_alert_gate(
        qualification=_qualification(),
        strategy_validation=_strategy_validation(),
        plan=_plan(),
        snapshot=_snapshot(),
        now=open_time,
    )
    assert gate["allowed"] is True
    assert gate["blockers"] == []
    assert gate["observed_risk_reward_target_1"] >= 1.0
    assert gate["observed_risk_reward_target_2"] >= 2.0


def test_final_alert_gate_blocks_low_risk_reward():
    open_time = datetime(2026, 8, 10, 11, 0, tzinfo=NY)
    plan = _plan()
    plan["target_1"] = 10.6
    plan["target_2"] = 11.2
    gate = final_trade_alert_gate(
        qualification=_qualification(),
        strategy_validation=_strategy_validation(),
        plan=plan,
        snapshot=_snapshot(),
        now=open_time,
    )
    assert gate["allowed"] is False
    assert "RISK_REWARD_T1_TOO_LOW" in gate["blockers"]
    assert "RISK_REWARD_T2_TOO_LOW" in gate["blockers"]
    assert gate["observed_risk_reward_target_1"] < 1.0
    assert gate["observed_risk_reward_target_2"] < 2.0


def test_final_alert_gate_blocks_mature_negative_strategy_calibration():
    open_time = datetime(2026, 8, 10, 11, 0, tzinfo=NY)
    gate = final_trade_alert_gate(
        qualification=_qualification(),
        strategy_validation=_strategy_validation("MATURE NEGATIVE"),
        plan=_plan(),
        snapshot=_snapshot(),
        now=open_time,
    )
    assert gate["allowed"] is False
    assert "STRATEGY_CALIBRATION_NEGATIVE" in gate["blockers"]


def test_final_alert_gate_blocks_stale_quote_and_wide_spread():
    open_time = datetime(2026, 8, 10, 11, 0, tzinfo=NY)
    gate = final_trade_alert_gate(
        qualification=_qualification(),
        strategy_validation=_strategy_validation(),
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
        dedup_key="strategy:1:HOD_BREAKOUT",
        ticker="TEST",
        session_id=1,
        stage="STRATEGY_MATCH",
        status="KNOWN SETUP RECOGNIZED",
    )
    second, event2 = store.record_once(
        dedup_key="strategy:1:HOD_BREAKOUT",
        ticker="TEST",
        session_id=1,
        stage="STRATEGY_MATCH",
        status="KNOWN SETUP RECOGNIZED",
    )
    assert first is True
    assert second is False
    assert event1["id"] == event2["id"]


def test_existing_plan_can_be_matched_by_setup_instance(tmp_path: Path):
    database = tmp_path / "pipeline.db"
    store = LivePipelineStore(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE trade_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT,
                created_at TEXT,
                decision TEXT,
                snapshot_json TEXT,
                result_json TEXT
            )
            """
        )
        snapshot = {
            "alpaca_market_context": {"session_id": 7},
            "strategy_context": {
                "primary": {
                    "strategy_id": "HOD_BREAKOUT",
                    "instance_key": "HOD_BREAKOUT|20260810T1100|10.0000",
                }
            },
        }
        connection.execute(
            "INSERT INTO trade_plans (ticker, created_at, decision, snapshot_json, result_json) VALUES (?, ?, ?, ?, ?)",
            (
                "TEST",
                "2026-08-10T11:00:00-04:00",
                "WATCH",
                json.dumps(snapshot),
                json.dumps({"decision": "WATCH"}),
            ),
        )

    found = store.existing_trade_plan_for_session(
        7,
        strategy_id="HOD_BREAKOUT",
        setup_instance_key="HOD_BREAKOUT|20260810T1100|10.0000",
    )
    not_found = store.existing_trade_plan_for_session(
        7,
        strategy_id="HOD_BREAKOUT",
        setup_instance_key="HOD_BREAKOUT|20260810T1110|10.2000",
    )
    assert found is not None
    assert not_found is None


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
