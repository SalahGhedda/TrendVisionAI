from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import trade_plan_calibration as v2


base = v2.base
TRADE_PLAN_VERSION = 3
MAX_SNAPSHOT_AGE_SECONDS = 45
MAX_TRADE_AGE_SECONDS = 45
MAX_QUOTE_AGE_SECONDS = 45
MAX_BAR_AGE_SECONDS = 90
MAX_ACTIONABLE_OBSERVED_SPREAD_PCT = 15.0

# Keep the same exception type so the existing v2 UI can show a clean blocked
# message without charging an OpenAI request when market context is unusable.
StaleMarketDataError = v2.StaleMarketDataError


def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def _parse_time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def _seconds_between(later: datetime, earlier: datetime) -> float:
    try:
        return (later - earlier).total_seconds()
    except TypeError:
        return (later.replace(tzinfo=None) - earlier.replace(tzinfo=None)).total_seconds()


def _age_seconds(value: Any, *, now: datetime) -> float | None:
    parsed = _parse_time(value)
    if parsed is None:
        return None
    return max(0.0, _seconds_between(now, parsed))


def _is_fresh(age: float | None, limit: int) -> bool:
    return age is not None and age <= limit


def _normalize_unobserved_zero_borrow(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Treat legacy zero_borrow=False as UNKNOWN rather than evidence of borrow."""
    value = copy.deepcopy(snapshot)
    trendvision = value.get("trendvision") or {}
    memory = trendvision.get("ticker_memory") or {}
    facts = memory.get("latest_known_facts") or {}
    zero_fact = facts.get("zero_borrow")
    if isinstance(zero_fact, dict) and zero_fact.get("value") is False:
        facts.pop("zero_borrow", None)

    convergence = trendvision.get("recent_convergence") or {}
    for event in convergence.get("events") or []:
        data = event.get("data") or {}
        if data.get("zero_borrow") is False:
            data.pop("zero_borrow", None)
    return value


def annotate_event_freshness(
    market: dict[str, Any],
    *,
    latest_sample: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Separate API-poll freshness from the timestamps of trade/quote/bar events."""
    value = copy.deepcopy(market)
    latest = value.setdefault("latest", {})
    if latest_sample:
        # v1 market context intentionally omitted these event timestamps. v3
        # restores them from the stored raw Alpaca sample when available.
        for key in (
            "captured_at",
            "trade_timestamp",
            "quote_timestamp",
            "minute_timestamp",
            "trade_price",
            "bid",
            "ask",
            "spread_pct",
            "minute_close",
            "minute_volume",
        ):
            if latest_sample.get(key) is not None:
                latest[key] = latest_sample.get(key)

    clock = now or _now()
    snapshot_age = _age_seconds(latest.get("captured_at"), now=clock)
    trade_age = _age_seconds(latest.get("trade_timestamp"), now=clock)
    quote_age = _age_seconds(latest.get("quote_timestamp"), now=clock)
    bar_age = _age_seconds(latest.get("minute_timestamp"), now=clock)

    snapshot_fresh = _is_fresh(snapshot_age, MAX_SNAPSHOT_AGE_SECONDS)
    trade_fresh = snapshot_fresh and _is_fresh(trade_age, MAX_TRADE_AGE_SECONDS)
    quote_fresh = snapshot_fresh and _is_fresh(quote_age, MAX_QUOTE_AGE_SECONDS)
    bar_fresh = snapshot_fresh and _is_fresh(bar_age, MAX_BAR_AGE_SECONDS)

    feed = str(value.get("feed") or "").strip().lower()
    delayed = feed == "delayed_sip"

    trade_price = base._num(latest.get("trade_price"))
    bid = base._num(latest.get("bid"))
    ask = base._num(latest.get("ask"))
    planning_price = None
    planning_source = "NONE"
    if trade_fresh and trade_price is not None and trade_price > 0:
        planning_price = trade_price
        planning_source = "FRESH_LATEST_TRADE"
    elif quote_fresh and bid is not None and ask is not None and bid > 0 and ask >= bid:
        planning_price = (bid + ask) / 2.0
        planning_source = "FRESH_QUOTE_MIDPOINT"

    current_context_usable = bool(
        value.get("available")
        and snapshot_fresh
        and not delayed
        and planning_price is not None
    )

    value["market_event_freshness"] = {
        "snapshot_received_age_seconds": snapshot_age,
        "snapshot_received_fresh": snapshot_fresh,
        "latest_trade_age_seconds": trade_age,
        "latest_trade_fresh": trade_fresh,
        "latest_quote_age_seconds": quote_age,
        "latest_quote_fresh": quote_fresh,
        "minute_bar_age_seconds": bar_age,
        "minute_bar_fresh": bar_fresh,
        "snapshot_limit_seconds": MAX_SNAPSHOT_AGE_SECONDS,
        "trade_limit_seconds": MAX_TRADE_AGE_SECONDS,
        "quote_limit_seconds": MAX_QUOTE_AGE_SECONDS,
        "bar_limit_seconds": MAX_BAR_AGE_SECONDS,
    }
    value["planning_price"] = planning_price
    value["planning_price_source"] = planning_source
    value["exact_current_price_usable"] = current_context_usable
    value["current_context_usable"] = current_context_usable
    value["freshness_interpretation"] = (
        "A freshly downloaded snapshot does not make an old latestTrade current. "
        "Use only event fields whose own timestamps are marked fresh."
    )
    return value


def _latest_raw_sample(database_path: str | Path, session_id: Any) -> dict[str, Any]:
    try:
        store = base.MarketDataStore(database_path)
        metrics = store.session_metrics(int(session_id))
        latest = metrics.get("last_sample") or {}
        return latest if isinstance(latest, dict) else {}
    except Exception:
        return {}


def build_trade_plan_snapshot_v3(
    *,
    database_path: str | Path,
    trendvision_snapshot: dict[str, Any],
    latest_ai_review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot = v2.build_trade_plan_snapshot_v2(
        database_path=database_path,
        trendvision_snapshot=trendvision_snapshot,
        latest_ai_review=latest_ai_review,
    )
    snapshot = _normalize_unobserved_zero_borrow(snapshot)
    snapshot["trade_plan_version"] = TRADE_PLAN_VERSION

    market = snapshot.get("alpaca_market_context") or {}
    latest_sample = _latest_raw_sample(database_path, market.get("session_id"))
    snapshot["alpaca_market_context"] = annotate_event_freshness(
        market,
        latest_sample=latest_sample,
    )

    snapshot["chart_interpretation_rules"] = [
        "Use candles, wicks, visible support/resistance, VWAP/EMA relationships, consolidation and breakout/pullback structure.",
        "IGNORE any third-party BUY, SELL, ENTRY, STOP, SL, TARGET, TP, risk/reward or trade-recommendation overlays visible in the screenshot.",
        "Do not copy, anchor to, validate or criticize the numerical trade levels printed by another tool in the screenshot; derive any v3 plan independently.",
        "If recommendation overlays obscure the underlying chart, reduce chart-structure confidence rather than using the overlay as evidence.",
    ]
    snapshot["cross_feed_rules"] = [
        "TrendVision and Alpaca IEX may use different venues, windows and definitions.",
        "Do not directly confirm or contradict TrendVision RV/1V/volume using IEX minute volume unless identical methodology is explicitly supplied.",
        "Different RV values are not automatically contradictory.",
        "A stale IEX latestTrade must not be called the current price even if the API snapshot itself was downloaded moments ago.",
    ]
    snapshot["supported_future_observations"] = [
        "new Alpaca samples from the selected feed, using each trade/quote/bar event's own timestamp and freshness",
        "subsequent TrendVision scanner events already supported by the app: all-in-one, volume, squeeze, whale, halt, news and explicit 0-borrow",
        "a newer user-supplied chart screenshot for visible pullback, consolidation, support/resistance, EMA/VWAP or breakout structure",
    ]
    snapshot["important_limitations"] = list(
        dict.fromkeys(
            [
                *(snapshot.get("important_limitations") or []),
                "Trade Plan v3 separates snapshot-receipt age from trade, quote and minute-bar event ages.",
                "Legacy zero_borrow=false means not observed/UNKNOWN unless an explicit 0-borrow event exists.",
                "Third-party trade recommendations printed inside the screenshot are ignored as evidence.",
                "IEX volume/quotes are partial-venue observations and are not directly comparable to differently defined TrendVision volume/RV measurements.",
            ]
        )
    )
    return snapshot


_SCHEMA_V3 = copy.deepcopy(base._TRADE_PLAN_SCHEMA)
_SCHEMA_V3["name"] = "trendvision_trade_plan_v3"


_INSTRUCTIONS_V3 = """You are the experimental Trade Plan v3 review layer of TrendVisionAI.
The human user decides and executes every trade manually. Your output is an experimental LONG-side plan saved for later objective calibration; it is not an order and profit is never guaranteed.

SOURCE AND FRESHNESS RULES:
- TrendVision structured data is the source for scanner convergence/signals and explicit Discord facts.
- Alpaca is the source for exact numerical market observations, but EVERY event has its own timestamp. Read market_event_freshness before using trade, quote or minute-bar fields.
- A fresh API snapshot can contain an old latestTrade. If latest_trade_fresh=false, NEVER describe trade_price as current/fresh and never use it as the current planning price.
- Use alpaca_market_context.planning_price and planning_price_source for current numerical planning context. If current_context_usable=false, actionable levels are forbidden.
- IEX_PARTIAL_VENUE is not consolidated SIP/NBBO. Never call IEX consolidated and never infer full-market depth/spread from one IEX quote.
- A screenshot price and Alpaca observation at different timestamps/feeds are a timing/feed discrepancy, not a contradiction by default.

TRENDVISION SEMANTICS:
- Legacy zero_borrow=false means 0-borrow was not observed. It is UNKNOWN, not proof that shares are available and not evidence against a squeeze.
- Only an explicit 0-borrow/no-shares-available observation counts as zero-borrow evidence.
- Different RV/relative-volume or volume numbers from TrendVision and IEX may use different venues, windows, baselines and definitions. Do not call them confirming or contradictory unless identical methodology is explicitly supplied.

SCREENSHOT RULES:
- Use the screenshot only for underlying visual chart structure: candles, wicks, trend, pullback/breakout, support/resistance, consolidation, visible VWAP/EMA relationships and visible volume shape.
- IGNORE any BUY, SELL, ENTRY, STOP, SL, TARGET, TP, R/R or other trade-recommendation overlays printed by another tool in the screenshot.
- Do not copy, anchor to, validate, compare against, or quote those recommendation levels in your plan or reasoning. Derive any entry/stop/targets independently from underlying chart structure plus fresh Alpaca context.
- If an overlay obscures the chart, lower chart confidence.

RISK RULES:
- Small float/market cap can amplify volatility, slippage, halt sensitivity and liquidity risk. Size alone does not prove manipulation, pumping or fraud.
- One IEX bid/ask size is partial-venue evidence only; do not claim a larger order will necessarily move the full market.
- Very extended moves, multiple halts, violent wicks, failed breakouts or very wide fresh observed spreads materially increase risk.

TRADE PLAN RULES:
- POTENTIAL TRADE requires current_context_usable=true, coherent chart evidence, entry zone, stop below entry and targets above entry.
- Entry must follow support/breakout/pullback/confirmation logic; stop must represent invalidation; targets must follow visible structure/resistance or defensible R/R.
- Do not choose POTENTIAL TRADE merely because attention/RV/momentum is high.
- Dangerous/unclear structure or EXTREME risk should normally stay WATCH/REJECT.

WHAT TO CONFIRM:
- Request only future observations listed in supported_future_observations.
- Do not request external consolidated prints/NBBO, Level 2/order book, broker depth, options flow, or other feeds not integrated in TrendVisionAI.

Return only the requested structured result."""


_OVERLAY_TERMS = re.compile(
    r"(?:potential\s+buy|potential\s+sell|potential\s+sl|potential\s+tp|suggested\s+(?:buy|sell|entry|stop|target)|\b(?:buy|sell|entry|stop|target)\s+(?:level|price)|\bSL\b|\bTP\b)",
    re.IGNORECASE,
)

_EXTERNAL_FEED_TERMS = re.compile(
    r"(?:\bnbbo\b|consolidated\s+(?:prints?|quotes?|venues?|market)|level\s*2|order[- ]?book|broker\s+depth|options?\s+(?:flow|activity))",
    re.IGNORECASE,
)


def _clean_text_v3(text: Any, snapshot: dict[str, Any]) -> str:
    value = v2._clean_text(text)
    if not value:
        return ""
    value = value.replace("by v1", "by v3").replace("by v2", "by v3")
    value = re.sub(r"consolidated/active\s+feed", "fresh selected Alpaca feed", value, flags=re.IGNORECASE)

    low = value.casefold()
    market = snapshot.get("alpaca_market_context") or {}
    freshness = market.get("market_event_freshness") or {}

    if _OVERLAY_TERMS.search(value) and any(term in low for term in ("screenshot", "chart", "panel", "overlay")):
        return (
            "A third-party trade-recommendation overlay is visible in the screenshot; its BUY/SL/TP levels are ignored. "
            "Only the underlying chart structure is used."
        )

    if not freshness.get("latest_trade_fresh") and any(term in low for term in ("trade_price", "latest trade", "alpaca trade")):
        if any(term in low for term in ("current", "fresh", "mismatch", "discrep", "conflict", "align")):
            age = freshness.get("latest_trade_age_seconds")
            age_text = f"{float(age):.0f}s" if age is not None else "unknown age"
            return (
                f"The latest Alpaca trade event is stale ({age_text}) and is not treated as the current price. "
                "Use fresh quote/trade context according to market_event_freshness."
            )

    if "zero-borrow" in low or "zero borrow" in low or "zero_borrow" in low:
        if any(term in low for term in ("false", "not a forced squeeze", "shares available", "against a squeeze")):
            return "No explicit 0-borrow event was observed; borrow availability remains UNKNOWN."

    if any(term in low for term in ("minute_volume", "minute volume", "iex volume")) and any(
        term in low for term in ("supporting", "confirms", "confirming", "contradicts", "proves", "matches scanner")
    ):
        return (
            "Alpaca/IEX volume and TrendVision scanner volume/RV may use different venues, windows and definitions; "
            "they are not treated as directly confirming or contradicting each other."
        )

    return value


def _clean_items_v3(values: Any, snapshot: dict[str, Any]) -> list[str]:
    cleaned = [_clean_text_v3(value, snapshot) for value in values or []]
    return list(dict.fromkeys(value for value in cleaned if value))


def _clean_confirmations_v3(values: Any, snapshot: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for raw in values or []:
        text = _clean_text_v3(raw, snapshot)
        if not text or _EXTERNAL_FEED_TERMS.search(text):
            continue
        low = text.casefold()
        if "short interest" in low and "trendvision" not in low and "all-in-one" not in low:
            continue
        if "filing" in low and "trendvision" not in low and "all-in-one" not in low:
            continue
        result.append(text)

    if not result:
        result = [
            "Watch new Alpaca samples and use only trade/quote/bar fields whose own timestamps are fresh.",
            "Watch subsequent TrendVision scanner events for renewed convergence, halt, whale, news or explicit 0-borrow evidence.",
            "Use a newer chart screenshot to verify pullback, hold, consolidation or breakout confirmation from underlying chart structure.",
        ]
    return list(dict.fromkeys(result))[:8]


def calibrate_trade_plan_payload_v3(
    parsed: dict[str, Any],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    result = v2.calibrate_trade_plan_payload_v2(parsed, snapshot)

    for field in ("summary", "setup_type", "entry_trigger", "invalidation"):
        result[field] = _clean_text_v3(result.get(field), snapshot)
    for field in ("positive_factors", "risk_factors", "chart_observations"):
        result[field] = _clean_items_v3(result.get(field), snapshot)
    result["what_to_confirm"] = _clean_confirmations_v3(result.get("what_to_confirm"), snapshot)

    market = snapshot.get("alpaca_market_context") or {}
    freshness = market.get("market_event_freshness") or {}
    if not market.get("current_context_usable"):
        result["decision"] = "WATCH"
        for key in ("entry_low", "entry_high", "stop_loss", "target_1", "target_2"):
            result[key] = None
        result["risk_factors"] = list(
            dict.fromkeys(
                [
                    *result.get("risk_factors", []),
                    "No sufficiently fresh trade/quote event is available for actionable levels, even if the API snapshot itself was recently downloaded.",
                ]
            )
        )

    # Extremely wide fresh observed spreads are an execution-quality guardrail.
    # This threshold is deliberately conservative and is calibration logic, not
    # a claim that every narrower spread is safe or tradeable.
    spread_pct = base._num((market.get("latest") or {}).get("spread_pct"))
    if (
        str(result.get("decision") or "").upper() == "POTENTIAL TRADE"
        and freshness.get("latest_quote_fresh")
        and spread_pct is not None
        and spread_pct >= MAX_ACTIONABLE_OBSERVED_SPREAD_PCT
    ):
        result["decision"] = "WATCH"
        for key in ("entry_low", "entry_high", "stop_loss", "target_1", "target_2"):
            result[key] = None
        result["risk_factors"] = list(
            dict.fromkeys(
                [
                    *result.get("risk_factors", []),
                    f"Experimental v3 guardrail blocked actionable levels because the fresh observed {str(market.get('feed') or '').upper()} spread is {spread_pct:.1f}% (guardrail {MAX_ACTIONABLE_OBSERVED_SPREAD_PCT:.0f}%+).",
                ]
            )
        )

    return result


def _freshness_error(market: dict[str, Any]) -> str:
    freshness = market.get("market_event_freshness") or {}
    def fmt(key: str) -> str:
        value = freshness.get(key)
        return f"{float(value):.0f}s" if value is not None else "unknown"
    return (
        "No sufficiently fresh Alpaca trade/quote context is available for a trade plan "
        f"(snapshot {fmt('snapshot_received_age_seconds')}, trade {fmt('latest_trade_age_seconds')}, "
        f"quote {fmt('latest_quote_age_seconds')}). Wait for the next Market Tracking update and try again."
    )


def analyze_trade_plan_v3(
    snapshot: dict[str, Any],
    *,
    image_path: str | Path,
    api_key: str,
    model: str,
) -> base.TradePlanResult:
    from openai import OpenAI

    snapshot = _normalize_unobserved_zero_borrow(snapshot)
    market = snapshot.get("alpaca_market_context") or {}
    if not market.get("current_context_usable"):
        raise StaleMarketDataError(_freshness_error(market))

    image_url, image_sha = base._image_data_url(image_path)
    request_snapshot = copy.deepcopy(snapshot)
    request_snapshot["trade_plan_version"] = TRADE_PLAN_VERSION
    request_snapshot["chart_screenshot"] = {
        "sha256": image_sha,
        "role": "User-supplied current chart screenshot; third-party recommendation overlays must be ignored",
    }

    client = OpenAI(api_key=api_key)
    response = client.responses.create(
        model=model,
        instructions=_INSTRUCTIONS_V3,
        input=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": json.dumps(request_snapshot, ensure_ascii=False, separators=(",", ":")),
                    },
                    {"type": "input_image", "image_url": image_url, "detail": "high"},
                ],
            }
        ],
        text={"format": _SCHEMA_V3},
    )
    parsed = calibrate_trade_plan_payload_v3(json.loads(response.output_text), request_snapshot)
    entry_high = base._num(parsed.get("entry_high"))
    stop = base._num(parsed.get("stop_loss"))
    target_1 = base._num(parsed.get("target_1"))
    target_2 = base._num(parsed.get("target_2"))

    return base.TradePlanResult(
        ticker=str(request_snapshot.get("ticker") or "?").upper(),
        model=model,
        decision=str(parsed["decision"]),
        confidence=str(parsed["confidence"]),
        risk_level=str(parsed["risk_level"]),
        chart_structure=str(parsed["chart_structure"]),
        setup_type=str(parsed.get("setup_type") or ""),
        summary=str(parsed.get("summary") or ""),
        entry_low=base._num(parsed.get("entry_low")),
        entry_high=entry_high,
        stop_loss=stop,
        target_1=target_1,
        target_2=target_2,
        risk_reward_target_1=base._risk_reward(entry_high, stop, target_1),
        risk_reward_target_2=base._risk_reward(entry_high, stop, target_2),
        entry_trigger=str(parsed.get("entry_trigger") or ""),
        invalidation=str(parsed.get("invalidation") or ""),
        positive_factors=[str(value) for value in parsed.get("positive_factors") or []],
        risk_factors=[str(value) for value in parsed.get("risk_factors") or []],
        chart_observations=[str(value) for value in parsed.get("chart_observations") or []],
        what_to_confirm=[str(value) for value in parsed.get("what_to_confirm") or []],
        created_at=base._now_iso(),
        plan_version=TRADE_PLAN_VERSION,
    )
