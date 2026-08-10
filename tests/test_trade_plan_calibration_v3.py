from __future__ import annotations

from datetime import datetime, timedelta, timezone

from trendvision_ai.trade_plan_calibration_v3 import (
    MAX_ACTIONABLE_OBSERVED_SPREAD_PCT,
    _clean_text_v3,
    _normalize_unobserved_zero_borrow,
    annotate_event_freshness,
    calibrate_trade_plan_payload_v3,
)


def _now() -> datetime:
    return datetime(2026, 8, 10, 21, 20, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _market(*, trade_age: int, quote_age: int, snapshot_age: int = 2, spread_pct: float = 2.0):
    now = _now()
    return {
        "available": True,
        "feed": "iex",
        "feed_scope": "IEX_PARTIAL_VENUE",
        "latest": {
            "captured_at": _iso(now - timedelta(seconds=snapshot_age)),
            "trade_timestamp": _iso(now - timedelta(seconds=trade_age)),
            "quote_timestamp": _iso(now - timedelta(seconds=quote_age)),
            "minute_timestamp": _iso(now - timedelta(seconds=30)),
            "trade_price": 1.075,
            "bid": 1.30,
            "ask": 1.40,
            "spread_pct": spread_pct,
            "minute_close": 1.36,
        },
    }


def _parsed():
    return {
        "decision": "POTENTIAL TRADE",
        "confidence": "HIGH",
        "risk_level": "MODERATE",
        "chart_structure": "STRONG",
        "setup_type": "breakout pullback",
        "summary": "coherent setup",
        "entry_low": 1.31,
        "entry_high": 1.35,
        "stop_loss": 1.25,
        "target_1": 1.45,
        "target_2": 1.55,
        "entry_trigger": "hold support",
        "invalidation": "lose support",
        "positive_factors": [],
        "risk_factors": [],
        "chart_observations": [],
        "what_to_confirm": [],
    }


def test_stale_trade_but_fresh_quote_uses_quote_midpoint():
    result = annotate_event_freshness(_market(trade_age=180, quote_age=3), now=_now())
    assert result["market_event_freshness"]["latest_trade_fresh"] is False
    assert result["market_event_freshness"]["latest_quote_fresh"] is True
    assert result["current_context_usable"] is True
    assert result["planning_price_source"] == "FRESH_QUOTE_MIDPOINT"
    assert abs(result["planning_price"] - 1.35) < 1e-9


def test_stale_trade_and_quote_blocks_current_context():
    result = annotate_event_freshness(_market(trade_age=180, quote_age=120), now=_now())
    assert result["current_context_usable"] is False
    assert result["planning_price"] is None


def test_legacy_zero_borrow_false_becomes_unknown():
    snapshot = {
        "trendvision": {
            "ticker_memory": {
                "latest_known_facts": {
                    "zero_borrow": {"value": False, "source_channel": "all-in-one-scanner"},
                }
            },
            "recent_convergence": {
                "events": [
                    {"channel": "all-in-one-scanner", "data": {"zero_borrow": False, "relative_volume": 75}},
                ]
            },
        }
    }
    normalized = _normalize_unobserved_zero_borrow(snapshot)
    facts = normalized["trendvision"]["ticker_memory"]["latest_known_facts"]
    event_data = normalized["trendvision"]["recent_convergence"]["events"][0]["data"]
    assert "zero_borrow" not in facts
    assert "zero_borrow" not in event_data


def test_screenshot_trade_overlay_is_not_used_as_evidence():
    snapshot = {"alpaca_market_context": {"market_event_freshness": {}}}
    cleaned = _clean_text_v3(
        "Screenshot panel lists Potential BUY 1.36, Potential SL 1.14 and Potential TP 1.78.",
        snapshot,
    )
    assert "ignored" in cleaned.lower()
    assert "1.36" not in cleaned
    assert "1.78" not in cleaned


def test_stale_trade_is_not_called_current_price():
    market = annotate_event_freshness(_market(trade_age=180, quote_age=3), now=_now())
    snapshot = {"alpaca_market_context": market}
    cleaned = _clean_text_v3(
        "Fresh Alpaca trade_price 1.075 conflicts with the current screenshot.",
        snapshot,
    )
    assert "stale" in cleaned.lower()
    assert "current price" in cleaned.lower()


def test_extremely_wide_fresh_quote_blocks_potential_trade():
    market = annotate_event_freshness(
        _market(
            trade_age=3,
            quote_age=3,
            spread_pct=MAX_ACTIONABLE_OBSERVED_SPREAD_PCT + 1,
        ),
        now=_now(),
    )
    snapshot = {
        "alpaca_market_context": market,
        "trendvision": {"recent_convergence": {"events": []}},
    }
    result = calibrate_trade_plan_payload_v3(_parsed(), snapshot)
    assert result["decision"] == "WATCH"
    assert result["entry_low"] is None
    assert result["stop_loss"] is None
    assert result["target_1"] is None
