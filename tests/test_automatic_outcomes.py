from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from trendvision_ai.automatic_outcomes import (
    SCOPE_AI_REVIEW,
    SCOPE_MARKET_SESSION,
    AutomaticOutcomeStore,
    classify_market_path,
)
from trendvision_ai.market_data import MarketDataStore


def _sample(ticker: str, captured_at: datetime, price: float, index: int) -> dict:
    return {
        "ticker": ticker,
        "captured_at": captured_at.isoformat(timespec="seconds"),
        "feed": "iex",
        "trade_price": price,
        "trade_size": 100,
        "trade_timestamp": captured_at.isoformat(timespec="seconds"),
        "bid": price - 0.01,
        "ask": price + 0.01,
        "bid_size": 100,
        "ask_size": 100,
        "quote_timestamp": captured_at.isoformat(timespec="seconds"),
        "spread": 0.02,
        "spread_pct": 0.2,
        "minute_timestamp": f"minute-{index}",
        "minute_open": price,
        "minute_high": price,
        "minute_low": price,
        "minute_close": price,
        "minute_volume": 1000 + index,
        "minute_vwap": price,
        "day_volume": 100000,
        "raw_json": "{}",
    }


def test_classifier_detects_spike_then_reversal():
    result = classify_market_path(
        {
            "available": True,
            "target_minutes": 15,
            "horizon_complete": True,
            "fresh_to_horizon": True,
            "coverage_pct": 100.0,
            "sample_count": 61,
            "return_pct": -1.45,
            "mfe_pct": 22.46,
            "mae_pct": -2.90,
            "max_spread_pct": 0.8,
        }
    )

    assert result["label"] == "SPIKE THEN REVERSAL"
    assert result["confidence"] == "HIGH"


def test_classifier_detects_strong_up_continuation():
    result = classify_market_path(
        {
            "available": True,
            "target_minutes": 15,
            "horizon_complete": True,
            "fresh_to_horizon": True,
            "coverage_pct": 99.0,
            "sample_count": 60,
            "return_pct": 11.0,
            "mfe_pct": 15.0,
            "mae_pct": -2.0,
            "max_spread_pct": 0.5,
        }
    )

    assert result["label"] == "STRONG UP CONTINUATION"
    assert result["confidence"] == "HIGH"


def test_classifier_refuses_stale_horizon_data():
    result = classify_market_path(
        {
            "available": True,
            "target_minutes": 15,
            "horizon_complete": True,
            "fresh_to_horizon": False,
            "coverage_pct": 95.0,
            "sample_count": 50,
            "return_pct": 20.0,
            "mfe_pct": 25.0,
            "mae_pct": -2.0,
        }
    )

    assert result["label"] == "INSUFFICIENT DATA"


def test_session_outcome_is_created_automatically_after_15_minutes(tmp_path: Path):
    database = tmp_path / "automatic.db"
    market = MarketDataStore(database)
    automatic = AutomaticOutcomeStore(database)
    session = market.ensure_session(
        ticker="RDGT",
        trigger_tier="HIGH ATTENTION",
        trigger_score=12,
        feed="iex",
    )

    reference = datetime.now(timezone.utc).astimezone() - timedelta(minutes=20)
    for index in range(31):
        captured = reference + timedelta(seconds=index * 30)
        if index == 0:
            price = 10.0
        elif index == 12:
            price = 12.2
        elif index == 30:
            price = 9.9
        else:
            price = 10.5
        market.save_sample(session["id"], _sample("RDGT", captured, price, index))

    changed = automatic.refresh_due_session_outcomes()
    saved = automatic.get_outcome(
        scope=SCOPE_MARKET_SESSION,
        subject_id=session["id"],
        horizon_minutes=15,
    )

    assert changed >= 1
    assert saved is not None
    assert saved["label"] == "SPIKE THEN REVERSAL"
    assert saved["metrics"]["horizon_complete"] is True
    assert saved["metrics"]["fresh_to_horizon"] is True


def test_ai_review_gets_automatic_outcome_without_manual_label(tmp_path: Path):
    database = tmp_path / "review-auto.db"
    market = MarketDataStore(database)
    session = market.ensure_session(
        ticker="AUTO",
        trigger_tier="HIGH ATTENTION",
        trigger_score=15,
        feed="iex",
    )
    reference = datetime.now(timezone.utc).astimezone() - timedelta(minutes=20)

    for index in range(31):
        captured = reference + timedelta(seconds=index * 30)
        price = 10.0 + (index / 30.0)
        market.save_sample(session["id"], _sample("AUTO", captured, price, index))

    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE ai_reviews (
                id INTEGER PRIMARY KEY,
                ticker TEXT NOT NULL,
                created_at TEXT NOT NULL,
                model TEXT NOT NULL,
                result_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO ai_reviews (id, ticker, created_at, model, result_json) VALUES (1, ?, ?, ?, '{}')",
            ("AUTO", reference.isoformat(timespec="seconds"), "test-model"),
        )

    automatic = AutomaticOutcomeStore(database)
    automatic.refresh_due_review_outcomes()
    saved = automatic.get_outcome(
        scope=SCOPE_AI_REVIEW,
        subject_id=1,
        horizon_minutes=15,
    )

    assert saved is not None
    assert saved["label"] == "STRONG UP CONTINUATION"
