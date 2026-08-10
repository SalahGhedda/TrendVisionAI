from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import trade_plans as base


TRADE_PLAN_VERSION = 2
MAX_MARKET_SAMPLE_AGE_SECONDS = 45


class StaleMarketDataError(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def _parse_time(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _seconds_between(later: datetime, earlier: datetime) -> float:
    try:
        return (later - earlier).total_seconds()
    except TypeError:
        return (later.replace(tzinfo=None) - earlier.replace(tzinfo=None)).total_seconds()


def annotate_market_freshness(
    market: dict[str, Any],
    *,
    now: datetime | None = None,
    max_age_seconds: int = MAX_MARKET_SAMPLE_AGE_SECONDS,
) -> dict[str, Any]:
    """Add explicit freshness/feed-scope metadata to one Alpaca market context."""
    value = copy.deepcopy(market)
    latest = value.get("latest") or {}
    captured = _parse_time(latest.get("captured_at"))
    clock = now or _now()

    if captured is None:
        age = None
        freshness = "UNKNOWN"
        usable = False
    else:
        age = max(0.0, _seconds_between(clock, captured))
        freshness = "FRESH" if age <= max_age_seconds else "STALE"
        usable = freshness == "FRESH"

    feed = str(value.get("feed") or "").strip().lower()
    if feed == "iex":
        feed_scope = "IEX_PARTIAL_VENUE"
        scope_note = (
            "IEX observations are partial-venue observations, not consolidated SIP/NBBO. "
            "A wide IEX quote is execution-risk evidence but does not prove the consolidated market spread."
        )
    elif feed == "sip":
        feed_scope = "SIP_CONSOLIDATED"
        scope_note = "SIP is the consolidated US-equity feed when the account subscription permits it."
    elif feed == "delayed_sip":
        feed_scope = "DELAYED_SIP_CONSOLIDATED"
        scope_note = "Delayed SIP is consolidated but delayed and must not be treated as a current trade-planning quote."
        usable = False
        freshness = "STALE"
    else:
        feed_scope = "UNKNOWN"
        scope_note = "Feed scope is unknown; do not make consolidated-market claims."

    value["sample_age_seconds"] = age
    value["freshness_limit_seconds"] = int(max_age_seconds)
    value["freshness"] = freshness
    value["exact_current_price_usable"] = bool(usable and value.get("available"))
    value["feed_scope"] = feed_scope
    value["feed_scope_note"] = scope_note
    return value


def build_trade_plan_snapshot_v2(
    *,
    database_path: str | Path,
    trendvision_snapshot: dict[str, Any],
    latest_ai_review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot = base.build_trade_plan_snapshot(
        database_path=database_path,
        trendvision_snapshot=trendvision_snapshot,
        latest_ai_review=latest_ai_review,
    )
    snapshot["trade_plan_version"] = TRADE_PLAN_VERSION
    snapshot["alpaca_market_context"] = annotate_market_freshness(
        snapshot.get("alpaca_market_context") or {}
    )
    snapshot["supported_future_observations"] = [
        "new Alpaca samples from the currently selected feed, including trade price, quote, spread, minute OHLCV/VWAP and quote sizes",
        "subsequent TrendVision scanner events already supported by the application, including momentum/breakout, volume, squeeze, whale, halt, news and 0-borrow events",
        "explicit all-in-one fields such as RV, SI or SEC continuation only when Windows actually exposes them",
        "a newer user-supplied chart screenshot for visible pullback, consolidation, support/resistance, EMA/VWAP or breakout structure",
    ]
    snapshot["important_limitations"] = [
        "The human user decides whether to trade; no brokerage order is placed.",
        "Freshness is mandatory: exact Alpaca price/quote data must be no more than 45 seconds old for a trade plan.",
        "IEX is a partial-venue feed, not consolidated SIP/NBBO. Do not describe IEX as consolidated.",
        "Different prices at different timestamps are normal time-series observations, not contradictions.",
        "Different relative-volume values may use different windows, baselines or feeds and are not automatically contradictions.",
        "Discord Windows notifications can omit lower rich-embed fields; missing data remains unknown.",
        "Chart vision is qualitative; exact numerical market calculations should use fresh supplied Alpaca fields.",
        "Small float/market cap may increase volatility and liquidity sensitivity but does not by itself prove manipulation.",
        "The plan is measured afterward so the system can learn whether its proposed levels were useful.",
    ]
    return snapshot


_SCHEMA_V2 = copy.deepcopy(base._TRADE_PLAN_SCHEMA)
_SCHEMA_V2["name"] = "trendvision_trade_plan_v2"


_INSTRUCTIONS_V2 = """You are the experimental Trade Plan v2 review layer of TrendVisionAI.
You receive structured TrendVision Discord scanner evidence, current Alpaca observations captured by the app, a prior AI candidate review when one exists, and one user-supplied current chart screenshot.

The human user makes every trading decision manually. Your output is an experimental LONG-side plan that will be saved and objectively evaluated later. It is not an order and you must not promise profit.

SOURCE RULES:
- TrendVision structured data is the source for scanner convergence, signals, explicit RV, halts, borrow/0-borrow, whale direction, exposed news and other captured Discord facts.
- Alpaca is the source for exact numerical market observations supplied in alpaca_market_context.
- Respect alpaca_market_context.freshness, sample_age_seconds and feed_scope.
- IEX_PARTIAL_VENUE is NOT consolidated SIP/NBBO. Never call IEX consolidated and never infer the full-market NBBO or depth from one IEX quote.
- The screenshot is qualitative evidence for visual structure: trend shape, breakout/pullback, visible support/resistance, wicks, consolidation, extension and visible indicators.
- Different prices observed at different timestamps are normal time-series updates. Do not label them a data conflict merely because they differ.
- A screenshot price and an Alpaca price are only a true contradiction if their timestamps are demonstrably aligned and the same measurement is expected. Otherwise describe a discrepancy or timing/feed difference without calling it contradictory.
- Different relative-volume values are not automatically contradictory. Their windows, baselines and feeds may differ unless the supplied data proves they are directly comparable.
- Small float or small market cap can amplify volatility, slippage and liquidity sensitivity. Do not claim manipulation, a pump, or fraud from size alone.
- One small IEX bid/ask size is partial-market evidence only. Do not claim that a larger order will necessarily move the whole market.

TRADE-PLAN RULES:
- If exact_current_price_usable is false, do not create actionable levels. Choose WATCH or REJECT and return null entry/stop/targets.
- Very extended moves, multiple halts, large observed spreads, violent wicks, failed breakouts or reversal structure materially increase risk.
- POTENTIAL TRADE requires a coherent long plan: entry zone, stop below entry and targets above entry.
- Entry must be tied to visible support, breakout, pullback or confirmation logic rather than a generic percentage.
- Stop must represent setup invalidation rather than a generic percentage.
- Targets should be tied to visible structure/resistance or a defensible reward/risk objective.
- Do not choose POTENTIAL TRADE merely because attention, RV or momentum is high.
- Dangerous/unclear chart structure or extreme risk should normally remain WATCH/REJECT.

WHAT TO CONFIRM:
- Request only observations TrendVisionAI can actually obtain from supported_future_observations.
- Do not ask the user to obtain external NBBO/consolidated prints, Level 2/order-book data, options flow, broker depth, or other non-integrated feeds.
- If mentioning SI, SEC, news or borrow, phrase it as a subsequent TrendVision/all-in-one observation only when Windows exposes it.

Return only the requested structured result."""


_UNSUPPORTED_CONFIRMATION = re.compile(
    r"(?:\bnbbo\b|consolidated\s+(?:prints?|venues?|quotes?|market)|level\s*2|order[- ]?book|options?\s+(?:flow|activity)|across\s+consolidated\s+venues|nasdaq\s+prints?|broker\s+depth)",
    re.IGNORECASE,
)


def _clean_text(text: Any) -> str:
    value = str(text or "").strip()
    if not value:
        return value

    replacements = (
        (r"consolidated\s+IEX\s+feed", "Alpaca IEX observation"),
        (r"consolidated\s+IEX", "Alpaca IEX"),
        (r"IEX\s+consolidated\s+feed", "Alpaca IEX observation"),
    )
    for pattern, replacement in replacements:
        value = re.sub(pattern, replacement, value, flags=re.IGNORECASE)

    low = value.casefold()
    if ("relative volume" in low or " rv" in f" {low}") and any(
        word in low for word in ("conflict", "contradict", "inconsistent")
    ):
        return (
            "Different relative-volume measurements are visible, but their windows/baselines/feeds may differ; "
            "they are not treated as contradictory without a directly comparable definition."
        )

    if any(term in low for term in ("small float", "low float", "market cap", "small-cap", "small cap")) and any(
        term in low for term in ("manipulation", "pump", "fraud")
    ):
        return (
            "Small float/market cap can amplify volatility, slippage and liquidity sensitivity; "
            "size alone does not demonstrate manipulation or a pump."
        )

    if "larger orders" in low and "move" in low and "price" in low:
        return (
            "Small observed IEX quote size suggests execution caution, but one partial-venue quote is not enough "
            "to infer full-market depth or how a larger order would move price."
        )

    if "wide" in low and "spread" in low and "iex" in low and any(
        term in low for term in ("thin liquidity", "illiquid", "liquidity")
    ):
        return (
            "The observed Alpaca IEX spread is wide and increases execution/slippage uncertainty; "
            "because IEX is partial-venue data, this does not by itself establish the consolidated NBBO spread."
        )

    if any(term in low for term in ("price conflict", "pricing conflict", "conflict between")) and "alpaca" in low and any(
        term in low for term in ("screenshot", "chart", "panel")
    ):
        return (
            "The screenshot and Alpaca observations show different prices. Treat this as a timing/feed discrepancy, "
            "not a contradiction, unless their timestamps are demonstrably aligned."
        )

    return value


def _clean_items(values: Any) -> list[str]:
    cleaned = [_clean_text(value) for value in values or []]
    return list(dict.fromkeys(value for value in cleaned if value))


def _clean_confirmations(values: Any, snapshot: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for raw in values or []:
        text = _clean_text(raw)
        if not text:
            continue
        if _UNSUPPORTED_CONFIRMATION.search(text):
            continue

        low = text.casefold()
        if "filing" in low and "trendvision" not in low and "all-in-one" not in low:
            continue
        if "short interest" in low and "trendvision" not in low and "all-in-one" not in low:
            continue
        if "borrow" in low and not any(term in low for term in ("trendvision", "0-borrow", "all-in-one")):
            continue
        result.append(text)

    market = snapshot.get("alpaca_market_context") or {}
    if not market.get("exact_current_price_usable"):
        age = market.get("sample_age_seconds")
        age_text = f"{float(age):.0f}s" if age is not None else "unknown age"
        result.insert(
            0,
            f"Wait for a fresh Alpaca sample (<= {MAX_MARKET_SAMPLE_AGE_SECONDS}s old); latest sample age is {age_text}.",
        )

    if not result:
        result = [
            "Watch subsequent fresh Alpaca samples for price/quote/spread and minute-volume behavior.",
            "Watch subsequent TrendVision scanner events for renewed convergence, halt, whale, news or 0-borrow evidence.",
            "Use a newer chart screenshot to verify whether the visible setup forms a pullback, hold, consolidation or breakout confirmation.",
        ]
    return list(dict.fromkeys(result))[:8]


def calibrate_trade_plan_payload_v2(
    parsed: dict[str, Any],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    result = base.calibrate_trade_plan_payload(parsed, snapshot)

    result["summary"] = _clean_text(result.get("summary"))
    result["setup_type"] = _clean_text(result.get("setup_type"))
    result["entry_trigger"] = _clean_text(result.get("entry_trigger"))
    result["invalidation"] = _clean_text(result.get("invalidation"))
    result["positive_factors"] = _clean_items(result.get("positive_factors"))
    result["risk_factors"] = _clean_items(result.get("risk_factors"))
    result["chart_observations"] = _clean_items(result.get("chart_observations"))
    result["what_to_confirm"] = _clean_confirmations(result.get("what_to_confirm"), snapshot)

    market = snapshot.get("alpaca_market_context") or {}
    if not market.get("exact_current_price_usable"):
        age = market.get("sample_age_seconds")
        age_text = f"{float(age):.0f}s" if age is not None else "unknown"
        result["decision"] = "WATCH"
        for key in ("entry_low", "entry_high", "stop_loss", "target_1", "target_2"):
            result[key] = None
        result["risk_factors"] = list(
            dict.fromkeys(
                [
                    *result.get("risk_factors", []),
                    f"Exact market data is not fresh enough for actionable levels (latest Alpaca sample age: {age_text}; limit: {MAX_MARKET_SAMPLE_AGE_SECONDS}s).",
                ]
            )
        )

    result["risk_factors"] = [text.replace("by v1", "by v2") for text in result.get("risk_factors") or []]
    return result


def analyze_trade_plan_v2(
    snapshot: dict[str, Any],
    *,
    image_path: str | Path,
    api_key: str,
    model: str,
) -> base.TradePlanResult:
    from openai import OpenAI

    market = snapshot.get("alpaca_market_context") or {}
    if not market.get("exact_current_price_usable"):
        age = market.get("sample_age_seconds")
        age_text = f"{float(age):.0f}s" if age is not None else "unknown"
        raise StaleMarketDataError(
            f"Latest Alpaca sample is not fresh enough for a trade plan (age {age_text}; limit {MAX_MARKET_SAMPLE_AGE_SECONDS}s). Wait for the next Market Tracking update and try again."
        )

    image_url, image_sha = base._image_data_url(image_path)
    request_snapshot = copy.deepcopy(snapshot)
    request_snapshot["trade_plan_version"] = TRADE_PLAN_VERSION
    request_snapshot["chart_screenshot"] = {
        "sha256": image_sha,
        "role": "User-supplied current chart screenshot",
    }

    client = OpenAI(api_key=api_key)
    response = client.responses.create(
        model=model,
        instructions=_INSTRUCTIONS_V2,
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
        text={"format": _SCHEMA_V2},
    )
    parsed = calibrate_trade_plan_payload_v2(json.loads(response.output_text), request_snapshot)
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
