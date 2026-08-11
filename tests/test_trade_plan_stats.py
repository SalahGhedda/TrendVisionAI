from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from trendvision_ai.qualification import CandidateQualificationEngine
from trendvision_ai.trade_plan_stats import TradePlanStatsEngine
from trendvision_ai.trade_plans import TradePlanResult, TradePlanStore


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _result(ticker: str = "TEST") -> TradePlanResult:
    return TradePlanResult(
        ticker=ticker,
        model="test-model",
        decision="POTENTIAL TRADE",
        confidence="HIGH",
        risk_level="MODERATE",
        chart_structure="STRONG",
        setup_type="High-of-Day Breakout",
        summary="synthetic",
        entry_low=10.0,
        entry_high=10.2,
        stop_loss=9.5,
        target_1=11.0,
        target_2=12.0,
        risk_reward_target_1=1.33,
        risk_reward_target_2=3.0,
        entry_trigger="hold support",
        invalidation="lose support",
        positive_factors=[],
        risk_factors=[],
        chart_observations=[],
        what_to_confirm=[],
        created_at=_now(),
        plan_version=3,
    )


def _seed_feature_snapshot(database: Path, session_id: int, tags: list[str]) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS calibration_feature_snapshots (
                session_id INTEGER PRIMARY KEY,
                ticker TEXT NOT NULL,
                trigger_at TEXT NOT NULL,
                feature_window_minutes INTEGER NOT NULL,
                feature_version INTEGER NOT NULL,
                built_at TEXT NOT NULL,
                features_json TEXT NOT NULL DEFAULT '{}',
                tags_json TEXT NOT NULL DEFAULT '[]'
            )
            """
        )
        connection.execute(
            """
            INSERT OR REPLACE INTO calibration_feature_snapshots (
                session_id, ticker, trigger_at, feature_window_minutes,
                feature_version, built_at, features_json, tags_json
            ) VALUES (?, 'TEST', ?, 30, 1, ?, '{}', ?)
            """,
            (session_id, _now(), _now(), json.dumps(tags)),
        )


def _add_plan(
    store: TradePlanStore,
    session_id: int,
    status: str,
    *,
    strategy_id: str | None = None,
) -> int:
    snapshot = {"ticker": "TEST", "alpaca_market_context": {"session_id": session_id}}
    if strategy_id:
        snapshot["strategy_context"] = {
            "recognized": True,
            "primary": {
                "strategy_id": strategy_id,
                "name": "High-of-Day Breakout",
                "family": "MOMENTUM_BREAKOUT",
                "score": 82,
            },
        }
    plan_id = store.save(_result(), snapshot, "chart.png")
    payload = {
        "status": status,
        "horizon_complete": True,
        "sample_count": 20,
        "entry_reached_at": _now(),
        "entry_price": 10.1,
        "target_1_hit_at": _now() if status in {"TARGET 1 ONLY", "TARGET 2 HIT", "TARGET 1 THEN STOP"} else None,
        "target_2_hit_at": _now() if status == "TARGET 2 HIT" else None,
        "stop_hit_at": _now() if status in {"STOP HIT FIRST", "TARGET 1 THEN STOP"} else None,
        "final_price": 11.0,
        "max_return_pct": 10.0,
        "max_drawdown_pct": -3.0,
    }
    store._save_evaluation(plan_id, 240, payload)
    return plan_id


def test_trade_plan_stats_aggregate_resolved_outcomes(tmp_path: Path):
    database = tmp_path / "stats.db"
    store = TradePlanStore(database)
    _seed_feature_snapshot(database, 1, ["ALL HIGH ATTENTION", "SIGNAL: BREAKOUT"])

    for status in ["TARGET 2 HIT", "TARGET 1 ONLY", "STOP HIT FIRST", "NO TARGET / NO STOP"]:
        _add_plan(store, 1, status)

    engine = TradePlanStatsEngine(database)
    pattern = engine.pattern_map(min_resolved=1)["SIGNAL: BREAKOUT"]
    assert pattern["resolved_count"] == 4
    assert pattern["t1_reached_count"] == 2
    assert pattern["t2_reached_count"] == 1
    assert pattern["stop_first_count"] == 1
    assert pattern["t1_reached_pct"] == 50.0
    assert pattern["stop_first_pct"] == 25.0


def test_calibration_is_immature_before_global_minimum(tmp_path: Path):
    database = tmp_path / "qualification-small.db"
    store = TradePlanStore(database)
    _seed_feature_snapshot(database, 1, ["ALL HIGH ATTENTION", "SIGNAL: BREAKOUT", "RV: 20x+"])
    for _ in range(15):
        _add_plan(store, 1, "TARGET 2 HIT")

    result = CandidateQualificationEngine(database).qualify_session(1)
    assert result["status"] == "INSUFFICIENT EVIDENCE"
    assert result["global_resolved"] == 15


def test_calibration_can_support_after_enough_specific_positive_history(tmp_path: Path):
    database = tmp_path / "qualification-ready.db"
    store = TradePlanStore(database)
    tags = ["ALL HIGH ATTENTION", "SIGNAL: BREAKOUT", "RV: 20x+"]
    _seed_feature_snapshot(database, 1, tags)
    for _ in range(30):
        _add_plan(store, 1, "TARGET 2 HIT")

    result = CandidateQualificationEngine(database).qualify_session(1)
    assert result["status"] == "EXPERIMENTALLY SUPPORTED"
    assert result["global_resolved"] == 30
    assert len(result["positive_patterns"]) >= 2
    assert all(row["pattern"] != "ALL HIGH ATTENTION" for row in result["positive_patterns"])
    assert not result["negative_patterns"]


def test_strategy_specific_history_is_grouped_and_validated(tmp_path: Path):
    database = tmp_path / "strategy-validation.db"
    store = TradePlanStore(database)
    _seed_feature_snapshot(database, 1, ["ALL HIGH ATTENTION", "SIGNAL: BREAKOUT"])
    for _ in range(15):
        _add_plan(store, 1, "TARGET 2 HIT", strategy_id="HOD_BREAKOUT")

    engine = TradePlanStatsEngine(database)
    strategy_row = engine.pattern_map(min_resolved=1)["STRATEGY: HOD_BREAKOUT"]
    assert strategy_row["resolved_count"] == 15
    assert strategy_row["t1_reached_pct"] == 100.0

    validation = CandidateQualificationEngine(database).validate_strategy_context(
        {
            "recognized": True,
            "primary": {
                "strategy_id": "HOD_BREAKOUT",
                "name": "High-of-Day Breakout",
                "family": "MOMENTUM_BREAKOUT",
            },
        }
    )
    assert validation["status"] == "MATURE POSITIVE"
