from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from trendvision_ai.market_data import MarketDataStore
from trendvision_ai.trade_plans import (
    TradePlanResult,
    TradePlanStore,
    calibrate_trade_plan_payload,
)


def _snapshot() -> dict:
    return {
        "ticker": "PLAN",
        "alpaca_market_context": {
            "available": True,
            "latest": {"trade_price": 10.1, "minute_close": 10.1},
        },
        "trendvision": {
            "recent_convergence": {
                "events": [],
            }
        },
    }


def _parsed() -> dict:
    return {
        "decision": "POTENTIAL TRADE",
        "confidence": "HIGH",
        "risk_level": "MODERATE",
        "chart_structure": "STRONG",
        "setup_type": "breakout pullback",
        "summary": "test",
        "entry_low": 10.0,
        "entry_high": 10.2,
        "stop_loss": 9.5,
        "target_1": 11.0,
        "target_2": 12.0,
        "entry_trigger": "hold the entry zone",
        "invalidation": "lose support",
        "positive_factors": [],
        "risk_factors": [],
        "chart_observations": [],
        "what_to_confirm": [],
    }


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


def _result(created_at: datetime) -> TradePlanResult:
    return TradePlanResult(
        ticker="PLAN",
        model="test-model",
        decision="POTENTIAL TRADE",
        confidence="HIGH",
        risk_level="MODERATE",
        chart_structure="STRONG",
        setup_type="breakout pullback",
        summary="test plan",
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
        created_at=created_at.isoformat(timespec="seconds"),
    )


def test_coherent_potential_trade_survives_guardrails():
    result = calibrate_trade_plan_payload(_parsed(), _snapshot())
    assert result["decision"] == "POTENTIAL TRADE"
    assert result["entry_low"] == 10.0
    assert result["target_2"] == 12.0


def test_extreme_risk_downgrades_and_removes_levels():
    parsed = _parsed()
    parsed["risk_level"] = "EXTREME"
    result = calibrate_trade_plan_payload(parsed, _snapshot())
    assert result["decision"] == "WATCH"
    assert result["entry_low"] is None
    assert result["stop_loss"] is None
    assert result["target_1"] is None


def test_trade_plan_evaluation_detects_target_2(tmp_path: Path):
    database = tmp_path / "trade-plan.db"
    market = MarketDataStore(database)
    store = TradePlanStore(database)
    session = market.ensure_session(
        ticker="PLAN",
        trigger_tier="HIGH ATTENTION",
        trigger_score=12,
        feed="iex",
    )

    created = datetime.now(timezone.utc).astimezone() - timedelta(minutes=20)
    plan_id = store.save(_result(created), _snapshot(), "chart.png")
    prices = [9.9, 10.1, 10.6, 11.1, 11.5, 12.1]
    for index, price in enumerate(prices):
        market.save_sample(
            int(session["id"]),
            _sample("PLAN", created + timedelta(minutes=index + 1), price, index),
        )

    evaluation = store.evaluate(plan_id)
    assert evaluation is not None
    assert evaluation["status"] == "TARGET 2 HIT"
    assert evaluation["entry_price"] == 10.1
    assert evaluation["target_1_hit_at"] is not None
    assert evaluation["target_2_hit_at"] is not None
    assert evaluation["stop_hit_at"] is None


def test_trade_plan_evaluation_detects_stop_first(tmp_path: Path):
    database = tmp_path / "trade-plan-stop.db"
    market = MarketDataStore(database)
    store = TradePlanStore(database)
    session = market.ensure_session(
        ticker="PLAN",
        trigger_tier="HIGH ATTENTION",
        trigger_score=12,
        feed="iex",
    )

    created = datetime.now(timezone.utc).astimezone() - timedelta(minutes=20)
    plan_id = store.save(_result(created), _snapshot(), "chart.png")
    for index, price in enumerate([10.1, 9.9, 9.4]):
        market.save_sample(
            int(session["id"]),
            _sample("PLAN", created + timedelta(minutes=index + 1), price, index),
        )

    evaluation = store.evaluate(plan_id)
    assert evaluation is not None
    assert evaluation["status"] == "STOP HIT FIRST"
    assert evaluation["stop_hit_at"] is not None
    assert evaluation["target_1_hit_at"] is None
