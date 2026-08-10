from pathlib import Path

from trendvision_ai.market_data import MarketDataStore, parse_snapshot


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
