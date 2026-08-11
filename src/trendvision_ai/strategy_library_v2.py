from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, time as clock_time
from typing import Any, Callable
from zoneinfo import ZoneInfo


STRATEGY_LIBRARY_VERSION = 2
NEW_YORK = ZoneInfo("America/New_York")
MIN_MATCH_SCORE = 70


@dataclass(slots=True)
class StrategyMatch:
    strategy_id: str
    name: str
    family: str
    score: int
    status: str
    rationale: list[str]
    key_levels: dict[str, float]
    metrics: dict[str, float | int | None]
    risk_notes: list[str]
    plan_constraints: dict[str, float]
    instance_key: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_STRATEGY_CATALOG = [
    {
        "strategy_id": "ORB_5M",
        "name": "5-Min Opening Range Breakout",
        "family": "OPENING_RANGE_BREAKOUT",
        "description": "Fresh break/hold above the first five regular-session minutes, with volatility-aware anti-chase limits.",
        "entry_framework": "Break/hold or controlled retest of the 5-minute opening-range high.",
        "invalidation_framework": "Failure back below the breakout area / opening-range structure.",
    },
    {
        "strategy_id": "ORB_15M",
        "name": "15-Min Opening Range Breakout",
        "family": "OPENING_RANGE_BREAKOUT",
        "description": "Fresh break/hold above the first fifteen regular-session minutes, with volatility-aware anti-chase limits.",
        "entry_framework": "Break/hold or controlled retest of the 15-minute opening-range high.",
        "invalidation_framework": "Loss of the opening-range breakout area or nearby structure.",
    },
    {
        "strategy_id": "PREMARKET_HIGH_BREAKOUT",
        "name": "Premarket High Breakout",
        "family": "PREMARKET_LEVEL_BREAKOUT",
        "description": "Uses 04:00-09:30 New York bars to mark the premarket high, then looks for a fresh regular-session break/hold through that level.",
        "entry_framework": "Fresh break/hold or controlled retest of the premarket high after the regular session opens.",
        "invalidation_framework": "Failure back below the premarket-high trigger / nearby breakout structure.",
    },
    {
        "strategy_id": "BREAKOUT_RETEST",
        "name": "Breakout + Retest",
        "family": "BREAKOUT_RETEST",
        "description": "Resistance breaks, price revisits the level in a controlled way, then reclaims/holds it.",
        "entry_framework": "Retest hold/reclaim near the broken resistance level.",
        "invalidation_framework": "Clean failure back below the broken resistance/retest structure.",
    },
    {
        "strategy_id": "HOD_BREAKOUT",
        "name": "High-of-Day Breakout",
        "family": "MOMENTUM_BREAKOUT",
        "description": "Fresh break through an established regular-session intraday high while adapting chase tolerance to recent 1-minute volatility.",
        "entry_framework": "Fresh break/hold above the prior regular-session high of day.",
        "invalidation_framework": "Failed breakout back below the prior high / trigger structure.",
    },
    {
        "strategy_id": "FIRST_PULLBACK",
        "name": "First Pullback After Momentum",
        "family": "MOMENTUM_PULLBACK",
        "description": "Strong impulse, controlled first retracement, then renewed upside pressure without a full impulse failure.",
        "entry_framework": "Bullish recovery from the first controlled pullback after the impulse.",
        "invalidation_framework": "Loss of the pullback low or breakdown of the impulse structure.",
    },
    {
        "strategy_id": "BULL_FLAG_BREAKOUT",
        "name": "Bull Flag / Tight Consolidation Breakout",
        "family": "MOMENTUM_CONTINUATION",
        "description": "Strong impulse followed by a relatively tight consolidation, then a fresh break through the flag high.",
        "entry_framework": "Fresh break/hold above the consolidation high after the flag forms.",
        "invalidation_framework": "Failure back into the flag or loss of the flag low.",
    },
    {
        "strategy_id": "VWAP_RECLAIM_HOLD",
        "name": "Session VWAP Reclaim + Hold",
        "family": "VWAP_RECLAIM",
        "description": "Price moves from below/at regular-session VWAP to above it and holds for consecutive bars.",
        "entry_framework": "Reclaim and hold above computed regular-session VWAP while price remains close enough to the level.",
        "invalidation_framework": "Loss of the reclaimed VWAP / nearby support structure.",
    },
    {
        "strategy_id": "VWAP_PULLBACK_HOLD",
        "name": "VWAP Pullback + Hold",
        "family": "VWAP_PULLBACK",
        "description": "An established move above VWAP pulls back toward VWAP and then shows renewed upside pressure while holding the area.",
        "entry_framework": "Bullish recovery after a controlled VWAP test/hold.",
        "invalidation_framework": "Clean loss of VWAP and the pullback low.",
    },
]


def strategy_catalog() -> list[dict[str, str]]:
    return [dict(item) for item in _STRATEGY_CATALOG]


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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


def _clean_bars(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw in bars:
        o = _num(raw.get("open"))
        h = _num(raw.get("high"))
        l = _num(raw.get("low"))
        c = _num(raw.get("close"))
        if any(value is None or value <= 0 for value in (o, h, l, c)):
            continue
        if h < max(o, c) or l > min(o, c) or h < l:
            continue
        item = dict(raw)
        item.update({"open": o, "high": h, "low": l, "close": c})
        item["volume"] = _num(raw.get("volume"))
        item["vwap"] = _num(raw.get("vwap"))
        item["parsed_time"] = _parse_time(raw.get("timestamp"))
        result.append(item)
    if result and all(item.get("parsed_time") is not None for item in result):
        result.sort(key=lambda item: item["parsed_time"])
    return result


def _ny_time(bar: dict[str, Any]) -> clock_time | None:
    parsed = bar.get("parsed_time")
    if parsed is None:
        return None
    return parsed.astimezone(NEW_YORK).time().replace(tzinfo=None)


def _regular_bars(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        bar
        for bar in bars
        if (local := _ny_time(bar)) is not None
        and clock_time(9, 30) <= local < clock_time(16, 0)
    ]


def _premarket_bars(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        bar
        for bar in bars
        if (local := _ny_time(bar)) is not None
        and clock_time(4, 0) <= local < clock_time(9, 30)
    ]


def _pct_above(value: float, reference: float) -> float:
    if reference <= 0:
        return 0.0
    return (value / reference - 1.0) * 100.0


def _close_location(bar: dict[str, Any]) -> float:
    high = float(bar["high"])
    low = float(bar["low"])
    if high <= low:
        return 0.5
    return max(0.0, min(1.0, (float(bar["close"]) - low) / (high - low)))


def _volume_ratio(bars: list[dict[str, Any]], index: int, lookback: int = 20) -> float | None:
    current = _num(bars[index].get("volume"))
    if current is None or current <= 0:
        return None
    values = [
        value
        for value in (
            _num(bar.get("volume"))
            for bar in bars[max(0, index - lookback) : index]
        )
        if value is not None and value > 0
    ]
    if len(values) < 3:
        return None
    baseline = statistics.median(values)
    return current / baseline if baseline > 0 else None


def _median_true_range_pct(bars: list[dict[str, Any]], lookback: int = 12) -> float | None:
    recent = bars[-max(3, int(lookback)) :]
    if len(recent) < 3:
        return None
    values: list[float] = []
    previous_close: float | None = None
    for bar in recent:
        high = float(bar["high"])
        low = float(bar["low"])
        close = float(bar["close"])
        if previous_close is None:
            true_range = high - low
        else:
            true_range = max(high - low, abs(high - previous_close), abs(low - previous_close))
        if close > 0:
            values.append(true_range / close * 100.0)
        previous_close = close
    return statistics.median(values) if values else None


def _adaptive_extension_limit(
    bars: list[dict[str, Any]],
    *,
    floor_pct: float,
    ceiling_pct: float,
    multiplier: float = 1.8,
) -> tuple[float, float | None]:
    volatility = _median_true_range_pct(bars)
    if volatility is None:
        return float(floor_pct), None
    return min(float(ceiling_pct), max(float(floor_pct), volatility * multiplier)), volatility


def _recent_cross_index(
    bars: list[dict[str, Any]],
    level: float,
    *,
    buffer_pct: float,
    recent_bars: int,
    start_index: int = 1,
) -> int | None:
    threshold = level * (1.0 + buffer_pct / 100.0)
    begin = max(start_index, len(bars) - max(1, recent_bars))
    for index in range(len(bars) - 1, begin - 1, -1):
        if index <= 0:
            continue
        previous_close = float(bars[index - 1]["close"])
        current_close = float(bars[index]["close"])
        if previous_close <= threshold and current_close > threshold:
            return index
    return None


def _time_key(bar: dict[str, Any] | None) -> str:
    if not bar:
        return "UNKNOWN"
    parsed = bar.get("parsed_time")
    if isinstance(parsed, datetime):
        return parsed.astimezone(NEW_YORK).strftime("%Y%m%dT%H%M")
    return str(bar.get("timestamp") or "UNKNOWN")


def _instance_key(strategy_id: str, trigger_bar: dict[str, Any] | None, reference: float) -> str:
    return f"{strategy_id}|{_time_key(trigger_bar)}|{reference:.4f}"


def _make_match(
    *,
    strategy_id: str,
    name: str,
    family: str,
    score: float,
    rationale: list[str],
    key_levels: dict[str, float],
    metrics: dict[str, float | int | None],
    risk_notes: list[str],
    max_entry_extension_pct: float,
    trigger_bar: dict[str, Any] | None,
) -> StrategyMatch | None:
    rounded = int(max(0, min(100, round(score))))
    if rounded < MIN_MATCH_SCORE:
        return None
    reference = float(key_levels.get("entry_reference") or 0.0)
    return StrategyMatch(
        strategy_id=strategy_id,
        name=name,
        family=family,
        score=rounded,
        status="CANDIDATE",
        rationale=list(dict.fromkeys(rationale)),
        key_levels={key: float(value) for key, value in key_levels.items()},
        metrics=metrics,
        risk_notes=list(dict.fromkeys(risk_notes)),
        plan_constraints={"max_entry_extension_pct": float(max_entry_extension_pct)},
        instance_key=_instance_key(strategy_id, trigger_bar, reference),
    )


def _opening_range_match(bars: list[dict[str, Any]], *, minutes: int) -> StrategyMatch | None:
    if len(bars) < minutes + 4:
        return None
    opening_end_minutes = 30 + minutes
    opening_end = clock_time(9 + opening_end_minutes // 60, opening_end_minutes % 60)
    opening = [
        bar for bar in bars
        if (local := _ny_time(bar)) is not None
        and clock_time(9, 30) <= local < opening_end
    ]
    after = [
        bar for bar in bars
        if (local := _ny_time(bar)) is not None
        and opening_end <= local < clock_time(16, 0)
    ]
    minimum_opening = 4 if minutes == 5 else 10
    if len(opening) < minimum_opening or len(after) < 2:
        return None

    opening_high = max(float(bar["high"]) for bar in opening)
    opening_low = min(float(bar["low"]) for bar in opening)
    combined = [*opening, *after]
    breakout = _recent_cross_index(
        combined,
        opening_high,
        buffer_pct=0.10,
        recent_bars=6,
        start_index=len(opening),
    )
    if breakout is None:
        return None
    latest_close = float(combined[-1]["close"])
    if latest_close <= opening_high:
        return None

    limit, volatility = _adaptive_extension_limit(
        combined, floor_pct=5.0, ceiling_pct=12.0
    )
    extension = _pct_above(latest_close, opening_high)
    if extension > limit * 1.35:
        return None

    volume_ratio = _volume_ratio(combined, breakout)
    score = 70.0
    rationale = [
        f"Price freshly crossed the {minutes}-minute opening-range high {opening_high:.4f}.",
        f"Current close is {extension:.2f}% above the trigger; volatility-aware anti-chase limit is {limit:.2f}%.",
    ]
    risk_notes: list[str] = []
    if volume_ratio is not None and volume_ratio >= 1.5:
        score += 10
        rationale.append(f"Breakout-bar volume was {volume_ratio:.2f}x its recent same-feed median.")
    if _close_location(combined[breakout]) >= 0.65:
        score += 8
    if extension <= limit * 0.65:
        score += 7
    else:
        risk_notes.append("Price is in the upper portion of the volatility-adjusted chase allowance; prefer a hold/retest over blind continuation entry.")
    range_pct = (opening_high / opening_low - 1.0) * 100.0 if opening_low > 0 else None
    if range_pct is not None and range_pct >= 20:
        score -= 5
        risk_notes.append(f"Opening range is very wide ({range_pct:.1f}%), increasing invalidation-distance risk.")

    return _make_match(
        strategy_id=f"ORB_{minutes}M",
        name=f"{minutes}-Min Opening Range Breakout",
        family="OPENING_RANGE_BREAKOUT",
        score=score,
        rationale=rationale,
        key_levels={
            "opening_range_high": opening_high,
            "opening_range_low": opening_low,
            "entry_reference": opening_high,
            "invalidation_reference": opening_high,
        },
        metrics={
            "opening_range_minutes": minutes,
            "opening_range_width_pct": range_pct,
            "breakout_volume_ratio": volume_ratio,
            "extension_from_trigger_pct": extension,
            "median_true_range_pct": volatility,
        },
        risk_notes=risk_notes,
        max_entry_extension_pct=limit,
        trigger_bar=combined[breakout],
    )


def _premarket_high_breakout_match(
    premarket: list[dict[str, Any]], regular: list[dict[str, Any]]
) -> StrategyMatch | None:
    if len(premarket) < 8 or len(regular) < 2:
        return None
    premarket_high = max(float(bar["high"]) for bar in premarket)
    premarket_low = min(float(bar["low"]) for bar in premarket)
    breakout = _recent_cross_index(
        regular,
        premarket_high,
        buffer_pct=0.08,
        recent_bars=6,
        start_index=1,
    )
    if breakout is None:
        return None
    latest_close = float(regular[-1]["close"])
    if latest_close <= premarket_high:
        return None

    limit, volatility = _adaptive_extension_limit(
        regular, floor_pct=5.0, ceiling_pct=12.0
    )
    extension = _pct_above(latest_close, premarket_high)
    if extension > limit * 1.35:
        return None

    volume_ratio = _volume_ratio(regular, breakout)
    pm_volume = sum(float(bar.get("volume") or 0.0) for bar in premarket)
    score = 74.0
    rationale = [
        f"Regular-session price freshly broke the premarket high {premarket_high:.4f}.",
        f"The current close is {extension:.2f}% above that level; volatility-aware anti-chase limit is {limit:.2f}%.",
    ]
    if volume_ratio is not None and volume_ratio >= 1.4:
        score += 10
        rationale.append(f"Breakout-bar volume was {volume_ratio:.2f}x its recent regular-session median.")
    if _close_location(regular[breakout]) >= 0.65:
        score += 7
    if extension <= limit * 0.65:
        score += 6
    risk_notes: list[str] = []
    if extension > limit * 0.65:
        risk_notes.append("The break is already extended relative to current 1-minute volatility; wait for a clean hold/retest rather than chasing.")

    return _make_match(
        strategy_id="PREMARKET_HIGH_BREAKOUT",
        name="Premarket High Breakout",
        family="PREMARKET_LEVEL_BREAKOUT",
        score=score,
        rationale=rationale,
        key_levels={
            "premarket_high": premarket_high,
            "premarket_low": premarket_low,
            "entry_reference": premarket_high,
            "invalidation_reference": premarket_high,
        },
        metrics={
            "premarket_bar_count": len(premarket),
            "premarket_volume": pm_volume,
            "breakout_volume_ratio": volume_ratio,
            "extension_from_trigger_pct": extension,
            "median_true_range_pct": volatility,
        },
        risk_notes=risk_notes,
        max_entry_extension_pct=limit,
        trigger_bar=regular[breakout],
    )


def _hod_breakout_match(bars: list[dict[str, Any]]) -> StrategyMatch | None:
    if len(bars) < 16:
        return None
    prior = bars[:-4]
    if len(prior) < 8:
        return None
    prior_high = max(float(bar["high"]) for bar in prior)
    breakout = _recent_cross_index(
        bars,
        prior_high,
        buffer_pct=0.10,
        recent_bars=5,
        start_index=max(1, len(prior)),
    )
    if breakout is None:
        return None
    latest_close = float(bars[-1]["close"])
    if latest_close <= prior_high:
        return None

    limit, volatility = _adaptive_extension_limit(
        bars, floor_pct=4.0, ceiling_pct=10.0
    )
    extension = _pct_above(latest_close, prior_high)
    if extension > limit * 1.35:
        return None
    volume_ratio = _volume_ratio(bars, breakout)
    score = 70.0
    rationale = [
        f"Price freshly broke the established regular-session high near {prior_high:.4f}.",
        f"Current close is {extension:.2f}% above the trigger; volatility-aware anti-chase limit is {limit:.2f}%.",
    ]
    if volume_ratio is not None and volume_ratio >= 1.4:
        score += 10
        rationale.append(f"Breakout-bar volume was {volume_ratio:.2f}x its recent same-feed median.")
    if _close_location(bars[breakout]) >= 0.65:
        score += 8
    if extension <= limit * 0.65:
        score += 7
    risk_notes: list[str] = []
    if extension > limit * 0.65:
        risk_notes.append("The move is already extended relative to recent 1-minute volatility; a pullback/retest may offer better structure.")

    return _make_match(
        strategy_id="HOD_BREAKOUT",
        name="High-of-Day Breakout",
        family="MOMENTUM_BREAKOUT",
        score=score,
        rationale=rationale,
        key_levels={
            "prior_high_of_day": prior_high,
            "entry_reference": prior_high,
            "invalidation_reference": prior_high,
        },
        metrics={
            "breakout_volume_ratio": volume_ratio,
            "extension_from_trigger_pct": extension,
            "median_true_range_pct": volatility,
        },
        risk_notes=risk_notes,
        max_entry_extension_pct=limit,
        trigger_bar=bars[breakout],
    )


def _breakout_retest_match(bars: list[dict[str, Any]]) -> StrategyMatch | None:
    if len(bars) < 22:
        return None
    base_start = max(0, len(bars) - 55)
    base_end = len(bars) - 8
    base = bars[base_start:base_end]
    if len(base) < 10:
        return None
    resistance = max(float(bar["high"]) for bar in base)

    breakout_index = None
    for index in range(base_end, len(bars) - 1):
        if float(bars[index]["close"]) > resistance * 1.002:
            breakout_index = index
            break
    if breakout_index is None:
        return None

    retest_index = None
    for index in range(breakout_index + 1, len(bars)):
        low = float(bars[index]["low"])
        close = float(bars[index]["close"])
        if resistance * 0.985 <= low <= resistance * 1.015 and close >= resistance * 0.995:
            retest_index = index
    if retest_index is None:
        return None

    latest_close = float(bars[-1]["close"])
    if latest_close <= resistance * 1.001:
        return None
    limit, volatility = _adaptive_extension_limit(
        bars, floor_pct=4.0, ceiling_pct=10.0
    )
    extension = _pct_above(latest_close, resistance)
    if extension > limit * 1.35:
        return None

    breakout_volume_ratio = _volume_ratio(bars, breakout_index)
    retest_low = float(bars[retest_index]["low"])
    score = 74.0
    rationale = [
        f"Resistance near {resistance:.4f} broke and was revisited in a controlled retest.",
        f"Price reclaimed/held above the level; current extension is {extension:.2f}% versus a {limit:.2f}% volatility-aware limit.",
    ]
    if breakout_volume_ratio is not None and breakout_volume_ratio >= 1.4:
        score += 8
    if extension <= limit * 0.65:
        score += 8
    risk_notes: list[str] = []
    if retest_low < resistance:
        risk_notes.append("The retest briefly traded below resistance; the plan should require a convincing hold/reclaim.")

    return _make_match(
        strategy_id="BREAKOUT_RETEST",
        name="Breakout + Retest",
        family="BREAKOUT_RETEST",
        score=score,
        rationale=rationale,
        key_levels={
            "broken_resistance": resistance,
            "retest_low": retest_low,
            "entry_reference": resistance,
            "invalidation_reference": retest_low,
        },
        metrics={
            "breakout_volume_ratio": breakout_volume_ratio,
            "extension_from_trigger_pct": extension,
            "bars_since_breakout": len(bars) - 1 - breakout_index,
            "median_true_range_pct": volatility,
        },
        risk_notes=risk_notes,
        max_entry_extension_pct=limit,
        trigger_bar=bars[retest_index],
    )


def _first_pullback_match(bars: list[dict[str, Any]]) -> StrategyMatch | None:
    if len(bars) < 18:
        return None
    window_start = max(0, len(bars) - 30)
    best: tuple[float, int, int] | None = None
    for start in range(window_start, len(bars) - 4):
        start_low = float(bars[start]["low"])
        if start_low <= 0:
            continue
        for peak in range(start + 2, len(bars) - 2):
            impulse_pct = _pct_above(float(bars[peak]["high"]), start_low)
            if best is None or impulse_pct > best[0]:
                best = (impulse_pct, start, peak)
    if best is None:
        return None
    impulse_pct, start, peak = best
    if impulse_pct < 4.0:
        return None

    start_low = float(bars[start]["low"])
    peak_high = float(bars[peak]["high"])
    after_peak = bars[peak + 1 :]
    if len(after_peak) < 2:
        return None
    pullback_low = min(float(bar["low"]) for bar in after_peak)
    pullback_index = min(
        range(peak + 1, len(bars)), key=lambda index: float(bars[index]["low"])
    )
    impulse_distance = peak_high - start_low
    if impulse_distance <= 0:
        return None
    retrace = (peak_high - pullback_low) / impulse_distance
    if retrace < 0.12 or retrace > 0.55:
        return None

    latest = bars[-1]
    previous = bars[-2]
    latest_close = float(latest["close"])
    bullish_recovery = latest_close > float(previous["high"]) or (
        latest_close > float(previous["close"]) and _close_location(latest) >= 0.65
    )
    if not bullish_recovery or latest_close < start_low + impulse_distance * 0.45:
        return None

    limit, volatility = _adaptive_extension_limit(
        bars, floor_pct=5.0, ceiling_pct=12.0
    )
    score = 72.0
    rationale = [
        f"A {impulse_pct:.1f}% momentum impulse was followed by a controlled {retrace * 100.0:.1f}% retracement.",
        "The latest bar shows renewed upside pressure after the pullback rather than a full impulse failure.",
    ]
    if retrace <= 0.38:
        score += 8
    volume_ratio = _volume_ratio(bars, len(bars) - 1)
    if volume_ratio is not None and volume_ratio >= 1.3:
        score += 7
    if impulse_pct >= 8.0:
        score += 5
    risk_notes: list[str] = []
    if retrace > 0.38:
        risk_notes.append("The pullback retraced a substantial part of the impulse; invalidation should respect the pullback low.")

    return _make_match(
        strategy_id="FIRST_PULLBACK",
        name="First Pullback After Momentum",
        family="MOMENTUM_PULLBACK",
        score=score,
        rationale=rationale,
        key_levels={
            "impulse_low": start_low,
            "impulse_high": peak_high,
            "pullback_low": pullback_low,
            "entry_reference": max(float(previous["high"]), latest_close),
            "invalidation_reference": pullback_low,
        },
        metrics={
            "impulse_pct": impulse_pct,
            "pullback_retrace_pct": retrace * 100.0,
            "recovery_volume_ratio": volume_ratio,
            "median_true_range_pct": volatility,
        },
        risk_notes=risk_notes,
        max_entry_extension_pct=limit,
        trigger_bar=bars[pullback_index],
    )


def _bull_flag_match(bars: list[dict[str, Any]]) -> StrategyMatch | None:
    if len(bars) < 14:
        return None
    latest_index = len(bars) - 1
    window_start = max(0, len(bars) - 32)
    best: tuple[float, int, int] | None = None
    for start in range(window_start, latest_index - 6):
        start_low = float(bars[start]["low"])
        if start_low <= 0:
            continue
        for peak in range(start + 2, latest_index - 2):
            if latest_index - peak > 10:
                continue
            impulse_pct = _pct_above(float(bars[peak]["high"]), start_low)
            if best is None or impulse_pct > best[0]:
                best = (impulse_pct, start, peak)
    if best is None or best[0] < 5.0:
        return None
    impulse_pct, start, peak = best
    flag = bars[peak + 1 : latest_index]
    if len(flag) < 2 or len(flag) > 9:
        return None

    impulse_low = float(bars[start]["low"])
    impulse_high = float(bars[peak]["high"])
    impulse_distance = impulse_high - impulse_low
    if impulse_distance <= 0:
        return None
    flag_high = max(float(bar["high"]) for bar in flag)
    flag_low = min(float(bar["low"]) for bar in flag)
    flag_depth = (impulse_high - flag_low) / impulse_distance
    if flag_depth < 0.05 or flag_depth > 0.50:
        return None
    flag_range_pct = (flag_high / flag_low - 1.0) * 100.0 if flag_low > 0 else 999.0
    if flag_range_pct > max(12.0, impulse_pct * 0.65):
        return None

    latest = bars[-1]
    previous = bars[-2]
    latest_close = float(latest["close"])
    if float(previous["close"]) > flag_high * 1.003:
        return None
    if latest_close <= flag_high * 1.001 or _close_location(latest) < 0.60:
        return None

    limit, volatility = _adaptive_extension_limit(
        bars, floor_pct=4.0, ceiling_pct=10.0
    )
    extension = _pct_above(latest_close, flag_high)
    if extension > limit * 1.35:
        return None
    volume_ratio = _volume_ratio(bars, latest_index)
    score = 76.0
    rationale = [
        f"A {impulse_pct:.1f}% impulse formed before a {len(flag)}-bar consolidation.",
        f"Price freshly broke the flag high {flag_high:.4f}; current extension is {extension:.2f}% versus a {limit:.2f}% volatility-aware limit.",
    ]
    if volume_ratio is not None and volume_ratio >= 1.3:
        score += 9
        rationale.append(f"Breakout-bar volume was {volume_ratio:.2f}x its recent median.")
    if flag_depth <= 0.35:
        score += 7
    risk_notes: list[str] = []
    if flag_depth > 0.40:
        risk_notes.append("The flag pulled back deeply relative to the impulse; use the flag low as a strict structural risk reference.")

    return _make_match(
        strategy_id="BULL_FLAG_BREAKOUT",
        name="Bull Flag / Tight Consolidation Breakout",
        family="MOMENTUM_CONTINUATION",
        score=score,
        rationale=rationale,
        key_levels={
            "impulse_low": impulse_low,
            "impulse_high": impulse_high,
            "flag_high": flag_high,
            "flag_low": flag_low,
            "entry_reference": flag_high,
            "invalidation_reference": flag_low,
        },
        metrics={
            "impulse_pct": impulse_pct,
            "flag_depth_pct_of_impulse": flag_depth * 100.0,
            "flag_range_pct": flag_range_pct,
            "breakout_volume_ratio": volume_ratio,
            "extension_from_trigger_pct": extension,
            "median_true_range_pct": volatility,
        },
        risk_notes=risk_notes,
        max_entry_extension_pct=limit,
        trigger_bar=latest,
    )


def _session_vwap_series(bars: list[dict[str, Any]]) -> list[float | None]:
    cumulative_value = 0.0
    cumulative_volume = 0.0
    result: list[float | None] = []
    for bar in bars:
        volume = _num(bar.get("volume"))
        if volume is None or volume <= 0:
            result.append(cumulative_value / cumulative_volume if cumulative_volume > 0 else None)
            continue
        bar_vwap = _num(bar.get("vwap"))
        if bar_vwap is None or bar_vwap <= 0:
            bar_vwap = (float(bar["high"]) + float(bar["low"]) + float(bar["close"])) / 3.0
        cumulative_value += bar_vwap * volume
        cumulative_volume += volume
        result.append(cumulative_value / cumulative_volume if cumulative_volume > 0 else None)
    return result


def _vwap_reclaim_match(bars: list[dict[str, Any]]) -> StrategyMatch | None:
    if len(bars) < 12:
        return None
    vwaps = _session_vwap_series(bars)
    if any(vwaps[index] is None for index in (-3, -2, -1)):
        return None
    v3, v2, v1 = float(vwaps[-3]), float(vwaps[-2]), float(vwaps[-1])
    close3, close2, close1 = (
        float(bars[-3]["close"]),
        float(bars[-2]["close"]),
        float(bars[-1]["close"]),
    )
    reclaimed = close3 <= v3 * 1.001 and close2 > v2 * 1.001 and close1 >= v1 * 1.001
    if not reclaimed:
        return None

    limit, volatility = _adaptive_extension_limit(
        bars, floor_pct=3.0, ceiling_pct=8.0
    )
    distance = _pct_above(close1, v1)
    if distance < 0 or distance > limit * 1.25:
        return None
    score = 72.0
    rationale = [
        f"Price moved from below/at regular-session VWAP to above it and held near {v1:.4f}.",
        f"Current distance above VWAP is {distance:.2f}% versus a {limit:.2f}% volatility-aware anti-chase limit.",
    ]
    volume_ratio = _volume_ratio(bars, len(bars) - 2)
    if volume_ratio is not None and volume_ratio >= 1.3:
        score += 8
    if close1 > close2:
        score += 6
    risk_notes: list[str] = []
    if distance > limit * 0.65:
        risk_notes.append("Price is already stretched above VWAP relative to recent 1-minute volatility; a retest may offer cleaner invalidation.")

    return _make_match(
        strategy_id="VWAP_RECLAIM_HOLD",
        name="Session VWAP Reclaim + Hold",
        family="VWAP_RECLAIM",
        score=score,
        rationale=rationale,
        key_levels={
            "session_vwap": v1,
            "entry_reference": v1,
            "invalidation_reference": v1,
        },
        metrics={
            "distance_above_vwap_pct": distance,
            "reclaim_volume_ratio": volume_ratio,
            "median_true_range_pct": volatility,
        },
        risk_notes=risk_notes,
        max_entry_extension_pct=limit,
        trigger_bar=bars[-2],
    )


def _vwap_pullback_hold_match(bars: list[dict[str, Any]]) -> StrategyMatch | None:
    if len(bars) < 15:
        return None
    vwaps = _session_vwap_series(bars)
    recent_start = max(3, len(bars) - 7)
    touch_index = None
    for index in range(recent_start, len(bars) - 1):
        vwap = vwaps[index]
        if vwap is None:
            continue
        low = float(bars[index]["low"])
        close = float(bars[index]["close"])
        if low <= float(vwap) * 1.012 and close >= float(vwap) * 0.995:
            touch_index = index
    if touch_index is None:
        return None

    prior_vwaps = [value for value in vwaps[max(0, touch_index - 8) : touch_index] if value is not None]
    if not prior_vwaps:
        return None
    established_above = any(
        float(bars[index]["high"]) >= float(vwaps[index] or 0.0) * 1.012
        for index in range(max(0, touch_index - 8), touch_index)
        if vwaps[index] is not None
    )
    if not established_above:
        return None

    latest = bars[-1]
    latest_vwap = vwaps[-1]
    if latest_vwap is None:
        return None
    latest_close = float(latest["close"])
    previous = bars[-2]
    if latest_close <= float(latest_vwap) * 1.001:
        return None
    if latest_close <= float(previous["close"]) or _close_location(latest) < 0.60:
        return None

    touch_low = float(bars[touch_index]["low"])
    limit, volatility = _adaptive_extension_limit(
        bars, floor_pct=3.0, ceiling_pct=8.0
    )
    distance = _pct_above(latest_close, float(latest_vwap))
    if distance > limit * 1.25:
        return None
    volume_ratio = _volume_ratio(bars, len(bars) - 1)
    score = 75.0
    rationale = [
        f"Price was established above VWAP, pulled back to test the area near {float(latest_vwap):.4f}, and is showing renewed upside pressure.",
        f"Current distance above VWAP is {distance:.2f}% versus a {limit:.2f}% volatility-aware limit.",
    ]
    if volume_ratio is not None and volume_ratio >= 1.25:
        score += 8
    if touch_low >= float(vwaps[touch_index] or latest_vwap) * 0.995:
        score += 6
    risk_notes: list[str] = []
    if touch_low < float(vwaps[touch_index] or latest_vwap):
        risk_notes.append("The pullback briefly pierced VWAP; require the recovery to hold rather than assuming VWAP support is intact.")

    return _make_match(
        strategy_id="VWAP_PULLBACK_HOLD",
        name="VWAP Pullback + Hold",
        family="VWAP_PULLBACK",
        score=score,
        rationale=rationale,
        key_levels={
            "session_vwap": float(latest_vwap),
            "pullback_low": touch_low,
            "entry_reference": max(float(latest_vwap), float(previous["high"])),
            "invalidation_reference": min(touch_low, float(latest_vwap)),
        },
        metrics={
            "distance_above_vwap_pct": distance,
            "recovery_volume_ratio": volume_ratio,
            "median_true_range_pct": volatility,
        },
        risk_notes=risk_notes,
        max_entry_extension_pct=limit,
        trigger_bar=latest,
    )


def _premarket_context(premarket: list[dict[str, Any]]) -> dict[str, Any]:
    if not premarket:
        return {
            "available": False,
            "bar_count": 0,
            "high": None,
            "low": None,
            "last_close": None,
            "volume": None,
        }
    return {
        "available": True,
        "bar_count": len(premarket),
        "high": max(float(bar["high"]) for bar in premarket),
        "low": min(float(bar["low"]) for bar in premarket),
        "last_close": float(premarket[-1]["close"]),
        "volume": sum(float(bar.get("volume") or 0.0) for bar in premarket),
        "first_timestamp": premarket[0].get("timestamp"),
        "last_timestamp": premarket[-1].get("timestamp"),
    }


def detect_known_setups(bars: list[dict[str, Any]]) -> dict[str, Any]:
    """Detect momentum-oriented intraday setup families from same-feed 1-minute bars.

    Version 2 adds premarket context, three continuation families, setup-instance
    identities, and volatility-aware anti-chase limits. Setup recognition remains
    deterministic and does not claim that any strategy is inherently profitable.
    """
    cleaned = _clean_bars(bars)
    regular = _regular_bars(cleaned)
    premarket = _premarket_bars(cleaned)
    matches: list[StrategyMatch] = []

    detectors: tuple[Callable[[], StrategyMatch | None], ...] = (
        lambda: _opening_range_match(regular, minutes=5),
        lambda: _opening_range_match(regular, minutes=15),
        lambda: _premarket_high_breakout_match(premarket, regular),
        lambda: _breakout_retest_match(regular),
        lambda: _hod_breakout_match(regular),
        lambda: _first_pullback_match(regular),
        lambda: _bull_flag_match(regular),
        lambda: _vwap_reclaim_match(regular),
        lambda: _vwap_pullback_hold_match(regular),
    )
    for detector in detectors:
        try:
            match = detector()
        except Exception:
            match = None
        if match is not None:
            matches.append(match)

    matches.sort(key=lambda item: (item.score, item.strategy_id), reverse=True)
    primary = matches[0] if matches else None
    return {
        "library_version": STRATEGY_LIBRARY_VERSION,
        "recognized": primary is not None,
        "primary": primary.to_dict() if primary is not None else None,
        "matches": [match.to_dict() for match in matches],
        "bar_count": len(cleaned),
        "regular_bar_count": len(regular),
        "premarket_context": _premarket_context(premarket),
        "policy": (
            "Known setup recognition is the primary strategy layer. Premarket context and recent volatility are structural inputs; historical TrendVisionAI calibration validates/filters the setup rather than inventing it."
        ),
        "summary": (
            f"Primary setup: {primary.name} (score {primary.score}/100, instance {primary.instance_key})."
            if primary is not None
            else "No configured known setup currently meets the deterministic candidate rules."
        ),
    }
