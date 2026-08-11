from __future__ import annotations

from datetime import datetime, timedelta, timezone

from trendvision_ai.live_pipeline import LivePipelineStore
from trendvision_ai.market_data import MarketDataStore
from trendvision_ai.missed_opportunities import MissedOpportunityAnalyzer


def _sample(ticker: str, captured_at: datetime, price: float, high: float | None = None) -> dict:
    return {
        "ticker": ticker,
        "captured_at": captured_at.astimezone().isoformat(timespec="seconds"),
        "feed": "iex",
        "trade_price": price,
        "trade_size": 100.0,
        "trade_timestamp": captured_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "bid": price - 0.01,
        "ask": price + 0.01,
        "bid_size": 100.0,
        "ask_size": 100.0,
        "quote_timestamp": captured_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "spread": 0.02,
        "spread_pct": 1.0,
        "minute_timestamp": captured_at.astimezone(timezone.utc).replace(second=0, microsecond=0).isoformat().replace("+00:00", "Z"),
        "minute_open": price,
        "minute_high": high if high is not None else price,
        "minute_low": price,
        "minute_close": price,
        "minute_volume": 10000.0,
        "minute_vwap": price,
        "day_volume": 100000.0,
        "raw_json": "{}",
    }


def test_major_runner_without_setup_is_diagnosed(tmp_path):
    database = tmp_path / "trendvision.db"
    market = MarketDataStore(database)
    session = market.ensure_session(
        ticker="RUNR",
        trigger_tier="HIGH ATTENTION",
        trigger_score=25,
        feed="iex",
        tracking_minutes=240,
    )
    session_id = int(session["id"])
    start = datetime.now().astimezone() - timedelta(minutes=10)
    market.save_sample(session_id, _sample("RUNR", start, 1.00))
    market.save_sample(session_id, _sample("RUNR", start + timedelta(minutes=2), 1.10, high=1.12))

    live = LivePipelineStore(database)
    live.record_once(
        dedup_key=f"strategy_scan:none:test:{session_id}",
        ticker="RUNR",
        session_id=session_id,
        stage="STRATEGY_SCAN",
        status="NO VALID SETUP",
    )

    rows = MissedOpportunityAnalyzer(database).recent_rows(limit=10, major_move_pct=8.0)
    row = next(item for item in rows if item["ticker"] == "RUNR")
    assert row["major_move"] is True
    assert row["diagnosis"] == "MISSED — NO SETUP"


def test_alerted_runner_is_not_counted_as_missed(tmp_path):
    database = tmp_path / "trendvision.db"
    market = MarketDataStore(database)
    session = market.ensure_session(
        ticker="ALRT",
        trigger_tier="HIGH ATTENTION",
        trigger_score=30,
        feed="iex",
        tracking_minutes=240,
    )
    session_id = int(session["id"])
    start = datetime.now().astimezone() - timedelta(minutes=10)
    market.save_sample(session_id, _sample("ALRT", start, 2.00))
    market.save_sample(session_id, _sample("ALRT", start + timedelta(minutes=3), 2.20, high=2.25))

    live = LivePipelineStore(database)
    live.record_once(
        dedup_key=f"final:test:{session_id}",
        ticker="ALRT",
        session_id=session_id,
        stage="FINAL_TRADE_ALERT",
        status="READY FOR MANUAL DECISION",
    )

    analyzer = MissedOpportunityAnalyzer(database)
    rows = analyzer.recent_rows(limit=10, major_move_pct=8.0)
    row = next(item for item in rows if item["ticker"] == "ALRT")
    assert row["diagnosis"] == "ALERTED"
    stats = analyzer.stats(rows)
    assert stats["alerted"] == 1
    assert stats["missed"] == 0
