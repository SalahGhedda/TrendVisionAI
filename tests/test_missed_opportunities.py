from __future__ import annotations

import sqlite3
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


def _set_event_time(database, event_id: int, when: datetime) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE live_pipeline_events SET created_at=? WHERE id=?",
            (when.astimezone().isoformat(timespec="seconds"), int(event_id)),
        )


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


def test_terra_watch_is_not_blamed_for_move_that_happened_before_decision(tmp_path):
    database = tmp_path / "trendvision.db"
    market = MarketDataStore(database)
    session = market.ensure_session(
        ticker="EARLY",
        trigger_tier="HIGH ATTENTION",
        trigger_score=35,
        feed="iex",
        tracking_minutes=240,
    )
    session_id = int(session["id"])
    now = datetime.now().astimezone()
    market.save_sample(session_id, _sample("EARLY", now - timedelta(minutes=10), 1.00))
    market.save_sample(session_id, _sample("EARLY", now - timedelta(minutes=4, seconds=50), 1.00))
    market.save_sample(session_id, _sample("EARLY", now - timedelta(minutes=3), 1.10, high=1.12))
    market.save_sample(session_id, _sample("EARLY", now - timedelta(minutes=1, seconds=50), 1.04))
    market.save_sample(session_id, _sample("EARLY", now - timedelta(minutes=1), 1.05, high=1.06))

    live = LivePipelineStore(database)
    _, strategy_event = live.record_once(
        dedup_key=f"strategy:test:{session_id}",
        ticker="EARLY",
        session_id=session_id,
        stage="STRATEGY_MATCH",
        status="KNOWN SETUP RECOGNIZED",
    )
    _, plan_event = live.record_once(
        dedup_key=f"plan:test:{session_id}",
        ticker="EARLY",
        session_id=session_id,
        stage="AUTO_PLAN",
        status="WATCH",
    )
    _set_event_time(database, int(strategy_event["id"]), now - timedelta(minutes=5))
    _set_event_time(database, int(plan_event["id"]), now - timedelta(minutes=2))

    analyzer = MissedOpportunityAnalyzer(database)
    rows = analyzer.recent_rows(limit=10, major_move_pct=8.0)
    row = next(item for item in rows if item["ticker"] == "EARLY")
    assert row["mfe_pct"] >= 8.0
    assert row["mfe_after_strategy_pct"] >= 8.0
    assert row["mfe_after_terra_pct"] < 8.0
    assert row["diagnosis"] == "FILTERED — NO MAJOR MOVE AFTER TERRA"
    stats = analyzer.stats(rows)
    assert stats["terra_missed"] == 0
    assert stats["terra_filtered_correctly"] == 1


def test_terra_watch_is_flagged_when_major_move_remains_after_decision(tmp_path):
    database = tmp_path / "trendvision.db"
    market = MarketDataStore(database)
    session = market.ensure_session(
        ticker="LATE",
        trigger_tier="HIGH ATTENTION",
        trigger_score=40,
        feed="iex",
        tracking_minutes=240,
    )
    session_id = int(session["id"])
    now = datetime.now().astimezone()
    market.save_sample(session_id, _sample("LATE", now - timedelta(minutes=10), 1.00))
    market.save_sample(session_id, _sample("LATE", now - timedelta(minutes=4, seconds=50), 1.00))
    market.save_sample(session_id, _sample("LATE", now - timedelta(minutes=3, seconds=50), 1.00))
    market.save_sample(session_id, _sample("LATE", now - timedelta(minutes=2), 1.10, high=1.12))
    market.save_sample(session_id, _sample("LATE", now - timedelta(minutes=1), 1.11, high=1.13))

    live = LivePipelineStore(database)
    _, strategy_event = live.record_once(
        dedup_key=f"strategy:test:{session_id}",
        ticker="LATE",
        session_id=session_id,
        stage="STRATEGY_MATCH",
        status="KNOWN SETUP RECOGNIZED",
    )
    _, plan_event = live.record_once(
        dedup_key=f"plan:test:{session_id}",
        ticker="LATE",
        session_id=session_id,
        stage="AUTO_PLAN",
        status="WATCH",
    )
    _set_event_time(database, int(strategy_event["id"]), now - timedelta(minutes=5))
    _set_event_time(database, int(plan_event["id"]), now - timedelta(minutes=4))

    analyzer = MissedOpportunityAnalyzer(database)
    rows = analyzer.recent_rows(limit=10, major_move_pct=8.0)
    row = next(item for item in rows if item["ticker"] == "LATE")
    assert row["mfe_after_terra_pct"] >= 8.0
    assert row["diagnosis"] == "MISSED — TERRA WATCH"
    stats = analyzer.stats(rows)
    assert stats["terra_missed"] == 1
