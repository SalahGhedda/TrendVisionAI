from __future__ import annotations

from datetime import datetime, timedelta, timezone

from trendvision_ai.trade_plan_calibration import (
    MAX_MARKET_SAMPLE_AGE_SECONDS,
    annotate_market_freshness,
    calibrate_trade_plan_payload_v2,
)


def _market(captured_at: datetime, *, feed: str = "iex") -> dict:
    return {
        "available": True,
        "feed": feed,
        "latest": {
            "captured_at": captured_at.isoformat(timespec="seconds"),
            "trade_price": 1.36,
            "minute_close": 1.36,
            "bid": 1.34,
            "ask": 1.38,
        },
    }


def _snapshot(market: dict) -> dict:
    return {
        "ticker": "V2",
        "alpaca_market_context": market,
        "trendvision": {"recent_convergence": {"events": []}},
    }


def _parsed() -> dict:
    return {
        "decision": "POTENTIAL TRADE",
        "confidence": "MEDIUM",
        "risk_level": "MODERATE",
        "chart_structure": "MODERATE",
        "setup_type": "breakout pullback",
        "summary": "Setup is being evaluated from the current chart and market context.",
        "entry_low": 1.34,
        "entry_high": 1.36,
        "stop_loss": 1.25,
        "target_1": 1.50,
        "target_2": 1.65,
        "entry_trigger": "Hold the pullback area.",
        "invalidation": "Lose visible support.",
        "positive_factors": [],
        "risk_factors": [],
        "chart_observations": [],
        "what_to_confirm": ["Watch a fresh Alpaca quote and spread."],
    }


def test_market_freshness_marks_recent_iex_as_fresh():
    now = datetime.now(timezone.utc).astimezone()
    market = annotate_market_freshness(
        _market(now - timedelta(seconds=15)),
        now=now,
    )
    assert market["freshness"] == "FRESH"
    assert market["exact_current_price_usable"] is True
    assert market["feed_scope"] == "IEX_PARTIAL_VENUE"
    assert market["sample_age_seconds"] <= MAX_MARKET_SAMPLE_AGE_SECONDS


def test_stale_market_data_removes_actionable_levels():
    now = datetime.now(timezone.utc).astimezone()
    market = annotate_market_freshness(
        _market(now - timedelta(seconds=90)),
        now=now,
    )
    result = calibrate_trade_plan_payload_v2(_parsed(), _snapshot(market))
    assert result["decision"] == "WATCH"
    assert result["entry_low"] is None
    assert result["stop_loss"] is None
    assert result["target_1"] is None
    assert any("not fresh enough" in item for item in result["risk_factors"])
    assert any("fresh Alpaca sample" in item for item in result["what_to_confirm"])


def test_delayed_sip_is_not_usable_for_current_trade_plan():
    now = datetime.now(timezone.utc).astimezone()
    market = annotate_market_freshness(
        _market(now - timedelta(seconds=5), feed="delayed_sip"),
        now=now,
    )
    assert market["feed_scope"] == "DELAYED_SIP_CONSOLIDATED"
    assert market["exact_current_price_usable"] is False


def test_v2_cleans_overclaims_and_unsupported_confirmations():
    now = datetime.now(timezone.utc).astimezone()
    market = annotate_market_freshness(_market(now - timedelta(seconds=5)), now=now)
    parsed = _parsed()
    parsed["risk_factors"] = [
        "Low float increases manipulation/pump risk.",
        "Small ask size means larger orders will move price.",
        "Very wide Alpaca IEX spread indicates thin liquidity.",
    ]
    parsed["what_to_confirm"] = [
        "Confirm real-time NBBO across consolidated venues.",
        "Check external short interest and borrow availability.",
        "Watch whether price holds VWAP on a pullback in a newer chart screenshot.",
    ]

    result = calibrate_trade_plan_payload_v2(parsed, _snapshot(market))
    joined_risks = " ".join(result["risk_factors"]).casefold()
    assert "does not demonstrate manipulation" in joined_risks
    assert "partial-venue quote" in joined_risks
    assert "consolidated nbbo" in joined_risks
    assert all("nbbo" not in item.casefold() for item in result["what_to_confirm"])
    assert all("external short interest" not in item.casefold() for item in result["what_to_confirm"])
    assert any("vwap" in item.casefold() for item in result["what_to_confirm"])


def test_different_rv_measurements_are_not_called_a_conflict():
    now = datetime.now(timezone.utc).astimezone()
    market = annotate_market_freshness(_market(now - timedelta(seconds=5)), now=now)
    parsed = _parsed()
    parsed["chart_observations"] = [
        "Relative volume 2.46x on the panel conflicts with the 75x RV scanner value."
    ]
    result = calibrate_trade_plan_payload_v2(parsed, _snapshot(market))
    text = " ".join(result["chart_observations"]).casefold()
    assert "not treated as contradictory" in text
    assert "windows/baselines/feeds" in text
