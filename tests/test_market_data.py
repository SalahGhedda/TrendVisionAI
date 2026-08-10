from datetime import datetime, timedelta, timezone
from pathlib import Path

from trendvision_ai.market_data import MarketDataStore, parse_snapshot
from trendvision_ai.review_outcomes import ReviewOutcomeStore


def test_parse_snapshot_extracts_trade_quote_and_bar():
    sample = parse_snapshot(
        "WYHG",
        {
            "latestTrade": {"p": 10.25, "s": 100, "t": "2026-08-10T16:00:01Z"},
            "latestQuote": {"bp": 10.20, "ap": 10.30, "bs": 4, "as": 6, "t": "2026-08-10T16:00:02Z"},
            "minuteBar": {
                "o": 9.90,
                "h": 10.50,
                "l": 9.80,
                "c": 10.25,
                "v": 287000,
                "vw": 10.11,
                "t": "2026-08-10T16:00:00Z",
            },
            "dailyBar": {"v": 3400000},
        },
        feed="iex",
    )

    assert sample["ticker"] == "WYHG"
    assert sample["trade_price"] == 10.25
    assert sample["bid"] == 10.20
    assert sample["ask"] == 10.30
    assert round(sample["spread_pct"], 4) == round(0.10 / 10.25 * 100, 4)
    assert sample["minute_high"] == 10.50
    assert sample["minute_low"] == 9.80
    assert sample["minute_volume"] == 287000
    assert sample["day_volume"] == 3400000


def test_market_store_builds_reference_mfe_and_mae(tmp_path: Path):
    store = MarketDataStore(tmp_path / "market.db")
    session = store.ensure_session(
        ticker="TEST",
        trigger_tier="HIGH ATTENTION",
        trigger_score=12,
        feed="iex",
        tracking_minutes=60,
    )

    store.save_sample(
        session["id"],
        {
            "ticker": "TEST",
            "captured_at": "2026-08-10T12:00:00-04:00",
            "feed": "iex",
            "trade_price": 10.0,
            "trade_size": 100,
            "trade_timestamp": "2026-08-10T16:00:00Z",
            "bid": 9.99,
            "ask": 10.01,
            "bid_size": 2,
            "ask_size": 2,
            "quote_timestamp": "2026-08-10T16:00:00Z",
            "spread": 0.02,
            "spread_pct": 0.2,
            "minute_timestamp": "2026-08-10T16:00:00Z",
            "minute_open": 9.8,
            "minute_high": 10.4,
            "minute_low": 9.7,
            "minute_close": 10.0,
            "minute_volume": 10000,
            "minute_vwap": 9.95,
            "day_volume": 100000,
            "raw_json": "{}",
        },
    )
    store.save_sample(
        session["id"],
        {
            "ticker": "TEST",
            "captured_at": "2026-08-10T12:01:00-04:00",
            "feed": "iex",
            "trade_price": 10.5,
            "trade_size": 50,
            "trade_timestamp": "2026-08-10T16:01:00Z",
            "bid": 10.49,
            "ask": 10.51,
            "bid_size": 1,
            "ask_size": 1,
            "quote_timestamp": "2026-08-10T16:01:00Z",
            "spread": 0.02,
            "spread_pct": 0.19,
            "minute_timestamp": "2026-08-10T16:01:00Z",
            "minute_open": 10.0,
            "minute_high": 11.0,
            "minute_low": 9.0,
            "minute_close": 10.5,
            "minute_volume": 15000,
            "minute_vwap": 10.3,
            "day_volume": 115000,
            "raw_json": "{}",
        },
    )

    metrics = store.session_metrics(session["id"])
    assert metrics["reference_price"] == 10.0
    assert metrics["last_price"] == 10.5
    assert round(metrics["return_pct"], 2) == 5.0
    assert round(metrics["mfe_pct"], 2) == 10.0
    assert round(metrics["mae_pct"], 2) == -10.0
    assert metrics["sample_count"] == 2


def _sample(
    ticker: str,
    captured_at: datetime,
    *,
    price: float,
    minute: str,
    high: float,
    low: float,
    volume: float,
):
    return {
        "ticker": ticker,
        "captured_at": captured_at.isoformat(timespec="seconds"),
        "feed": "iex",
        "trade_price": price,
        "trade_size": 10,
        "trade_timestamp": captured_at.isoformat(timespec="seconds"),
        "bid": price - 0.01,
        "ask": price + 0.01,
        "bid_size": 100,
        "ask_size": 100,
        "quote_timestamp": captured_at.isoformat(timespec="seconds"),
        "spread": 0.02,
        "spread_pct": 0.2,
        "minute_timestamp": minute,
        "minute_open": price,
        "minute_high": high,
        "minute_low": low,
        "minute_close": price,
        "minute_volume": volume,
        "minute_vwap": price,
        "day_volume": 10000,
        "raw_json": "{}",
    }


def test_review_metrics_use_review_timestamp_and_ignore_partial_reference_bar(tmp_path: Path):
    database = tmp_path / "review-market.db"
    store = MarketDataStore(database)
    session = store.ensure_session(
        ticker="TEST",
        trigger_tier="HIGH ATTENTION",
        trigger_score=12,
        feed="iex",
    )

    review_time = datetime.now(timezone.utc).astimezone() - timedelta(minutes=20)
    store.save_sample(
        session["id"],
        _sample(
            "TEST",
            review_time + timedelta(seconds=5),
            price=10.0,
            minute="minute-0",
            high=99.0,
            low=1.0,
            volume=100,
        ),
    )
    store.save_sample(
        session["id"],
        _sample(
            "TEST",
            review_time + timedelta(minutes=5),
            price=11.0,
            minute="minute-5",
            high=12.0,
            low=9.0,
            volume=200,
        ),
    )
    store.save_sample(
        session["id"],
        _sample(
            "TEST",
            review_time + timedelta(minutes=14, seconds=50),
            price=12.0,
            minute="minute-14",
            high=13.0,
            low=11.0,
            volume=300,
        ),
    )

    metrics = store.review_metrics(
        ticker="TEST",
        review_created_at=review_time.isoformat(timespec="seconds"),
        horizon_minutes=15,
    )

    assert metrics["available"] is True
    assert metrics["reference_price"] == 10.0
    assert round(metrics["return_pct"], 4) == 20.0
    assert round(metrics["mfe_pct"], 4) == 30.0
    assert round(metrics["mae_pct"], 4) == -10.0
    assert metrics["max_minute_volume"] == 300
    assert metrics["sample_count"] == 3
    assert metrics["peak_price"] == 13.0
    assert metrics["trough_price"] == 9.0


def test_saved_review_outcome_keeps_objective_market_snapshot(tmp_path: Path):
    store = ReviewOutcomeStore(tmp_path / "outcomes.db")
    market = {
        "available": True,
        "target_minutes": 30,
        "reference_price": 5.0,
        "return_pct": 8.0,
        "mfe_pct": 15.0,
        "mae_pct": -4.0,
        "sample_count": 100,
    }

    store.save_outcome(
        review_id=42,
        ticker="TEST",
        horizon_minutes=30,
        outcome="MODEST CONTINUATION",
        notes="objective calibration sample",
        followup={
            "event_count": 2,
            "channel_count": 2,
            "channels": ["a", "b"],
        },
        market_metrics=market,
    )

    saved = store.get_outcome(42)
    assert saved is not None
    assert saved["market_reference_price"] == 5.0
    assert saved["market_return_pct"] == 8.0
    assert saved["market_mfe_pct"] == 15.0
    assert saved["market_mae_pct"] == -4.0
    assert saved["market_sample_count"] == 100
    assert saved["market_metrics"]["target_minutes"] == 30
