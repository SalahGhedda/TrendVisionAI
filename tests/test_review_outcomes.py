import json
import sqlite3

from trendvision_ai.review_outcomes import ReviewOutcomeStore


def test_outcome_store_tracks_post_review_scanner_events(tmp_path):
    database = tmp_path / "trendvision.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE ai_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                created_at TEXT NOT NULL,
                model TEXT NOT NULL,
                result_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE scanner_events (
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
        result = {
            "review_version": 3,
            "interest_level": "VERY HIGH",
            "risk_level": "EXTREME",
            "evidence_quality": "MEDIUM",
            "review_status": "WAIT FOR CONFIRMATION",
        }
        connection.execute(
            "INSERT INTO ai_reviews (ticker, created_at, model, result_json) VALUES (?, ?, ?, ?)",
            (
                "WYHG",
                "2026-08-10T11:59:00-04:00",
                "gpt-5-mini",
                json.dumps(result),
            ),
        )
        connection.execute(
            "INSERT INTO scanner_events (received_at, channel, ticker, event_type, headline, data_json) VALUES (?, ?, ?, ?, ?, ?)",
            (
                "2026-08-10T11:58:00-04:00",
                "volume-scanner",
                "WYHG",
                "volume",
                "before review",
                "{}",
            ),
        )
        connection.execute(
            "INSERT INTO scanner_events (received_at, channel, ticker, event_type, headline, data_json) VALUES (?, ?, ?, ?, ?, ?)",
            (
                "2026-08-10T12:05:00-04:00",
                "halt-scanner",
                "WYHG",
                "halt",
                "after review halt",
                "{}",
            ),
        )
        connection.execute(
            "INSERT INTO scanner_events (received_at, channel, ticker, event_type, headline, data_json) VALUES (?, ?, ?, ?, ?, ?)",
            (
                "2026-08-10T12:10:00-04:00",
                "all-in-one-scanner",
                "WYHG",
                "all_in_one",
                "after review all-in-one",
                "{}",
            ),
        )

    store = ReviewOutcomeStore(database)
    record = store.latest_review_record("WYHG")
    assert record is not None

    followup = store.post_review_summary(
        ticker="WYHG",
        review_created_at=record["created_at"],
        horizon_minutes=30,
    )
    assert followup["event_count"] == 2
    assert followup["channel_count"] == 2

    store.save_outcome(
        review_id=record["id"],
        ticker="WYHG",
        horizon_minutes=30,
        outcome="TOO RISKY / UNTRADEABLE",
        notes="Repeated halts; no clean entry.",
        followup=followup,
    )
    saved = store.get_outcome(record["id"])
    assert saved is not None
    assert saved["outcome"] == "TOO RISKY / UNTRADEABLE"
    assert saved["followup_event_count"] == 2
