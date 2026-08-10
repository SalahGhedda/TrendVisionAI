from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from trendvision_ai.automatic_outcomes import SCOPE_MARKET_SESSION
from trendvision_ai.calibration_stats import CalibrationStatsEngine
from trendvision_ai.market_data import MarketDataStore


def _create_scanner_events_table(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS scanner_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                received_at TEXT NOT NULL,
                channel TEXT NOT NULL,
                ticker TEXT,
                event_type TEXT NOT NULL,
                headline TEXT NOT NULL,
                data_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )


def _insert_event(
    database: Path,
    *,
    received_at: datetime,
    channel: str,
    ticker: str,
    data: dict,
) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO scanner_events (
                received_at, channel, ticker, event_type, headline, data_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                received_at.isoformat(timespec="seconds"),
                channel,
                ticker,
                channel.replace("-", "_"),
                f"{ticker} test alert",
                json.dumps(data),
            ),
        )


def _insert_outcome(
    database: Path,
    *,
    session_id: int,
    ticker: str,
    reference_at: str,
    label: str,
    return_pct: float,
    mfe_pct: float,
    mae_pct: float,
) -> None:
    metrics = {
        "available": True,
        "target_minutes": 15,
        "horizon_complete": True,
        "fresh_to_horizon": True,
        "coverage_pct": 100.0,
        "sample_count": 61,
        "return_pct": return_pct,
        "mfe_pct": mfe_pct,
        "mae_pct": mae_pct,
    }
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO automatic_outcomes (
                scope, subject_id, ticker, reference_at, horizon_minutes,
                classified_at, label, confidence, reason, halt_count,
                flags_json, metrics_json
            ) VALUES (?, ?, ?, ?, 15, ?, ?, 'HIGH', 'test', 0, '[]', ?)
            """,
            (
                SCOPE_MARKET_SESSION,
                session_id,
                ticker,
                reference_at,
                datetime.now().astimezone().isoformat(timespec="seconds"),
                label,
                json.dumps(metrics),
            ),
        )


def test_feature_snapshot_uses_only_detection_time_conditions(tmp_path: Path):
    database = tmp_path / "stats.db"
    market = MarketDataStore(database)
    _create_scanner_events_table(database)
    session = market.ensure_session(
        ticker="TEST",
        trigger_tier="HIGH ATTENTION",
        trigger_score=14,
        feed="iex",
    )
    trigger = datetime.fromisoformat(session["started_at"])

    _insert_event(
        database,
        received_at=trigger - timedelta(minutes=8),
        channel="all-in-one-scanner",
        ticker="TEST",
        data={
            "signal": "BREAKOUT",
            "relative_volume": "12x",
            "change_pct": 45.0,
            "market_cap": "40M",
        },
    )
    _insert_event(
        database,
        received_at=trigger - timedelta(minutes=4),
        channel="volume-scanner",
        ticker="TEST",
        data={"relative_volume": "8x", "change_pct": 50.0},
    )
    _insert_event(
        database,
        received_at=trigger - timedelta(minutes=1),
        channel="news-scanner",
        ticker="TEST",
        data={"headline": "test catalyst"},
    )
    # This later alert must not leak into the frozen trigger feature set.
    _insert_event(
        database,
        received_at=trigger + timedelta(minutes=2),
        channel="halt-scanner",
        ticker="TEST",
        data={"halt_status": "HALTED UP"},
    )

    engine = CalibrationStatsEngine(database)
    snapshot = engine.ensure_feature_snapshot(session["id"])

    assert snapshot is not None
    features = snapshot["features"]
    tags = snapshot["tags"]
    assert features["channel_count"] == 3
    assert features["relative_volume_max"] == 12.0
    assert features["max_change_pct"] == 50.0
    assert features["market_cap_latest"] == 40_000_000.0
    assert features["pre_trigger_halt_count"] == 0
    assert "SIGNAL: BREAKOUT" in tags
    assert "COMPOUND: BREAKOUT + RV>=10x" in tags
    assert "COMPOUND: BREAKOUT + 3+ CHANNELS" in tags
    assert "MCAP: <50M" in tags


def test_pattern_statistics_aggregate_automatic_outcomes(tmp_path: Path):
    database = tmp_path / "aggregate.db"
    market = MarketDataStore(database)
    _create_scanner_events_table(database)
    engine = CalibrationStatsEngine(database)

    sessions = []
    for ticker, label, result in (
        ("AAA", "STRONG UP CONTINUATION", 10.0),
        ("BBB", "SPIKE THEN REVERSAL", -2.0),
    ):
        session = market.ensure_session(
            ticker=ticker,
            trigger_tier="HIGH ATTENTION",
            trigger_score=13,
            feed="iex",
        )
        trigger = datetime.fromisoformat(session["started_at"])
        _insert_event(
            database,
            received_at=trigger - timedelta(minutes=3),
            channel="all-in-one-scanner",
            ticker=ticker,
            data={"signal": "BREAKOUT", "relative_volume": "15x"},
        )
        _insert_event(
            database,
            received_at=trigger - timedelta(minutes=2),
            channel="volume-scanner",
            ticker=ticker,
            data={"relative_volume": "12x"},
        )
        _insert_event(
            database,
            received_at=trigger - timedelta(minutes=1),
            channel="news-scanner",
            ticker=ticker,
            data={},
        )
        engine.ensure_feature_snapshot(session["id"])
        _insert_outcome(
            database,
            session_id=session["id"],
            ticker=ticker,
            reference_at=session["started_at"],
            label=label,
            return_pct=result,
            mfe_pct=20.0 if ticker == "BBB" else 14.0,
            mae_pct=-3.0,
        )
        sessions.append(session)

    rows = engine.pattern_stats(horizon_minutes=15, min_samples=1)
    breakout = next(row for row in rows if row["pattern"] == "SIGNAL: BREAKOUT")

    assert breakout["sample_count"] == 2
    assert breakout["median_return_pct"] == 4.0
    assert breakout["up_continuation_pct"] == 50.0
    assert breakout["spike_reversal_pct"] == 50.0
    assert breakout["evidence"] == "TOO EARLY"
