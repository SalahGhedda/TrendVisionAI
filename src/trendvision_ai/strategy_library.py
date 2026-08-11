from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, time as clock_time
from typing import Any
from zoneinfo import ZoneInfo


STRATEGY_LIBRARY_VERSION = 1
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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_STRATEGY_CATALOG = [
    {
        "strategy_id": "ORB_5M",
        "name": "5-Min Opening Range Breakout",
        "family": "OPENING_RANGE_BREAKOUT",
        "description": "Looks for a fresh break above the first five regular-session minutes, with the current price still reasonably close to the breakout level.",
        "entry_framework": "Break/hold or controlled retest of the 5-minute opening-range high.",
        "invalidation_framework": "Failure back below the breakout area / opening-range structure.",
    },
    {
        "strategy_id": "ORB_15M",
        "name": "15-Min Opening Range Breakout",
        "family": "OPENING_RANGE_BREAKOUT",
        "description": "Looks for a fresh break above the first fifteen regular-session minutes rather than chasing a move that is already far above the opening range.",
        "entry_framework": "Break/hold or retest of the 15-minute opening-range high.",
        "invalidation_framework": "Loss of the opening-range breakout area or nearby structure.",
    },
    {
        "strategy_id": "BREAKOUT_RETEST",
        "name": "Breakout + Retest",
        "family": "BREAKOUT_RETEST",
        "description": "Looks for resistance to break, a controlled revisit of that level, and a reclaim/hold above it.",
        "entry_framework": "Retest hold/reclaim near the broken resistance level.",
        "invalidation_framework": "Clean failure back below the broken resistance/retest structure.",
    },
    {
        "strategy_id": "HOD_BREAKOUT",
        "name": "High-of-Day Breakout",
        "family": "MOMENTUM_BREAKOUT",
        "description": "Looks for a fresh break through an established intraday high, while avoiding entries that are already excessively extended above that level.",
        "entry_framework": "Fresh break/hold above the prior intraday high.",
        "invalidation_framework": "Failed breakout back below the prior high / trigger structure.",
    },
    {
        "strategy_id": "FIRST_PULLBACK",
        "name": "First Pullback After Momentum",
        "family": "MOMENTUM_PULLBACK",
        "description": "Looks for a strong impulse, a controlled first retracement, and renewed upside pressure without a full retrace of the impulse.",
        "entry_framework": "Bullish recovery from the first controlled pullback after the impulse.",
        "invalidation_framework": "Loss of the pullback low or breakdown of the impulse structure.",
    },
    {
        "strategy_id": "VWAP_RECLAIM_HOLD",
        "name": "Session VWAP Reclaim + Hold",
        "family": "VWAP_RECLAIM",
        "description": "Computes session cumulative VWAP from the same Alpaca bar feed, then looks for price to reclaim it and hold above it for consecutive bars.",
        "entry_framework": "Reclaim and hold above computed session VWAP while price remains close enough to the level.",
        "invalidation_framework": "Loss of the reclaimed session VWAP / nearby support structure.",
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
    previous = [
        _num(bar.get("volume"))
        for bar in bars[max(0, index - lookback) : index]
    ]
    values = [value for value in previous if value is not None and value > 0]
    if len(values) < 3:
        return None
    baseline = statistics.median(values)
    return current / baseline if baseline > 0 else None


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
    for index in range(begin, len(bars)):
        previous_close = float(bars[index - 1]["close"])
        current_close = float(bars[index]["close"])
        if previous_close <= threshold and current_close > threshold:
            return index
    return None


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
) -> StrategyMatch | None:
    rounded = int(max(0, min(100, round(score))))
    if rounded < MIN_MATCH_SCORE:
        return None
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
        plan_constraints={
            "max_entry_extension_pct": float(max_entry_extension_pct),
        },
    )


def _opening_range_match(
    bars: list[dict[str, Any]],
    *,
    minutes: int,
) -> StrategyMatch | None:
    timed = [bar for bar in bars if bar.get("parsed_time") is not None]
    if len(timed) < minutes + 4:
        return None

    opening_start = clock_time(9, 30)
    end_minute = 30 + minutes
    opening_end = clock_time(9 + end_minute // 60, end_minute % 60)

    opening: list[dict[str, Any]] = []
    after: list[dict[str, Any]] = []
    for bar in timed:
        eastern = bar["parsed_time"].astimezone(NEW_YORK)
        local = eastern.time().replace(tzinfo=None)
        if opening_start <= local < opening_end:
            opening.append(bar)
        elif opening_end <= local < clock_time(16, 0):
            after.append(bar)

    minimum_opening_bars = 4 if minutes == 5 else 10
    if len(opening) < minimum_opening_bars or len(after) < 2:
        return None

    opening_high = max(float(bar["high"]) for bar in opening)
    opening_low = min(float(bar["low"]) for bar in opening)
    combined = [*opening, *after]
    breakout = _recent_cross_index(
        combined,
        opening_high,
        buffer_pct=0.10,
        recent_bars=5,
        start_index=len(opening),
    )
    if breakout is None:
        return None

    latest = combined[-1]
    latest_close = float(latest["close"])
    if latest_close <= opening_high:
        return None
    extension = _pct_above(latest_close, opening_high)
    if extension > 7.0:
        return None

    volume_ratio = _volume_ratio(combined, breakout)
    close_location = _close_location(combined[breakout])
    score = 70.0
    rationale = [
        f"Price freshly crossed the {minutes}-minute opening-range high {opening_high:.4f}.",
        f"Current close remains {extension:.2f}% above the breakout reference rather than being extremely extended.",
    ]
    risk_notes: list[str] = []
    if volume_ratio is not None and volume_ratio >= 1.5:
        score += 10
        rationale.append(f"Breakout bar volume was {volume_ratio:.2f}x its recent same-feed median.")
    if close_location >= 0.65:
        score += 8
        rationale.append("The breakout bar closed in the upper portion of its range.")
    if extension <= 3.0:
        score += 7
    else:
        risk_notes.append(f"Price is already {extension:.2f}% above the opening-range trigger; avoid chasing further extension.")
    range_pct = (opening_high / opening_low - 1.0) * 100.0 if opening_low > 0 else None
    if range_pct is not None and range_pct >= 20:
        risk_notes.append(f"The opening range itself is very wide ({range_pct:.1f}%), increasing volatility/invalidation distance risk.")
        score -= 5

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
        },
        risk_notes=risk_notes,
        max_entry_extension_pct=5.0,
    )


def _hod_breakout_match(bars: list[dict[str, Any]]) -> StrategyMatch | None:
    if len(bars) < 16:
        return None
    prior = bars[:-4]
    recent = bars[-5:]
    if len(prior) < 8:
        return None
    prior_high = max(float(bar["high"]) for bar in prior)
    breakout = _recent_cross_index(
        bars,
        prior_high,
        buffer_pct=0.10,
        recent_bars=4,
        start_index=max(1, len(prior)),
    )
    if breakout is None:
        return None
    latest_close = float(bars[-1]["close"])
    if latest_close <= prior_high:
        return None
    extension = _pct_above(latest_close, prior_high)
    if extension > 6.0:
        return None

    volume_ratio = _volume_ratio(bars, breakout)
    score = 70.0
    rationale = [
        f"Price freshly broke the established intraday high near {prior_high:.4f}.",
        f"Current close is {extension:.2f}% above that trigger, inside the library's anti-chase limit.",
    ]
    risk_notes: list[str] = []
    if volume_ratio is not None and volume_ratio >= 1.4:
        score += 10
        rationale.append(f"Breakout bar volume was {volume_ratio:.2f}x its recent same-feed median.")
    if _close_location(bars[breakout]) >= 0.65:
        score += 8
        rationale.append("The breakout bar closed strongly within its own range.")
    if extension <= 2.5:
        score += 7
    else:
        risk_notes.append("The move is already several percent above the prior high; a pullback/retest may offer better structure than chasing.")

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
            "recent_bar_count": len(recent),
        },
        risk_notes=risk_notes,
        max_entry_extension_pct=4.0,
    )


def _breakout_retest_match(bars: list[dict[str, Any]]) -> StrategyMatch | None:
    if len(bars) < 22:
        return None
    base_start = max(0, len(bars) - 50)
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
    if breakout_index is None or breakout_index >= len(bars) - 1:
        return None

    retest_index = None
    for index in range(breakout_index + 1, len(bars)):
        low = float(bars[index]["low"])
        close = float(bars[index]["close"])
        if resistance * 0.985 <= low <= resistance * 1.012 and close >= resistance * 0.995:
            retest_index = index
            break
    if retest_index is None:
        return None

    latest_close = float(bars[-1]["close"])
    if latest_close <= resistance * 1.001:
        return None
    extension = _pct_above(latest_close, resistance)
    if extension > 6.0:
        return None

    breakout_volume_ratio = _volume_ratio(bars, breakout_index)
    retest_low = float(bars[retest_index]["low"])
    score = 74.0
    rationale = [
        f"Resistance near {resistance:.4f} broke before a controlled revisit of the level.",
        f"The retest low {retest_low:.4f} stayed within the configured tolerance and price reclaimed/held above resistance.",
    ]
    risk_notes: list[str] = []
    if breakout_volume_ratio is not None and breakout_volume_ratio >= 1.4:
        score += 8
        rationale.append(f"Breakout volume was {breakout_volume_ratio:.2f}x its recent same-feed median.")
    if extension <= 3.0:
        score += 8
    else:
        risk_notes.append("Price has moved several percent away from the retest level; do not convert this into a chase entry.")
    if retest_low < resistance:
        risk_notes.append("The retest briefly traded below the breakout level; the final plan should require a convincing hold/reclaim rather than assume support.")

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
        },
        risk_notes=risk_notes,
        max_entry_extension_pct=4.0,
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
            peak_high = float(bars[peak]["high"])
            impulse_pct = _pct_above(peak_high, start_low)
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
    if not bullish_recovery:
        return None
    if latest_close < start_low + impulse_distance * 0.45:
        return None

    score = 72.0
    rationale = [
        f"A {impulse_pct:.1f}% momentum impulse was followed by a controlled {retrace * 100.0:.1f}% retracement.",
        "The latest bar shows renewed upside pressure after the pullback rather than a full impulse failure.",
    ]
    risk_notes: list[str] = []
    if retrace <= 0.38:
        score += 8
    else:
        risk_notes.append("The pullback retraced a substantial part of the impulse; invalidation should respect the pullback low.")
    volume_ratio = _volume_ratio(bars, len(bars) - 1)
    if volume_ratio is not None and volume_ratio >= 1.3:
        score += 7
        rationale.append(f"Recovery-bar volume was {volume_ratio:.2f}x its recent same-feed median.")
    if impulse_pct >= 8.0:
        score += 5

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
        },
        risk_notes=risk_notes,
        max_entry_extension_pct=5.0,
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
            bar_vwap = (
                float(bar["high"]) + float(bar["low"]) + float(bar["close"])
            ) / 3.0
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
    v3 = float(vwaps[-3])
    v2 = float(vwaps[-2])
    v1 = float(vwaps[-1])
    close3 = float(bars[-3]["close"])
    close2 = float(bars[-2]["close"])
    close1 = float(bars[-1]["close"])

    reclaimed = close3 <= v3 * 1.001 and close2 > v2 * 1.001 and close1 >= v1 * 1.001
    if not reclaimed:
        return None
    distance = _pct_above(close1, v1)
    if distance < 0 or distance > 3.5:
        return None

    score = 72.0
    rationale = [
        f"Price moved from below/at computed session VWAP to above it and held for consecutive bars near {v1:.4f}.",
        f"Current close is only {distance:.2f}% above session VWAP, reducing chase risk relative to a late reclaim.",
    ]
    risk_notes: list[str] = []
    volume_ratio = _volume_ratio(bars, len(bars) - 2)
    if volume_ratio is not None and volume_ratio >= 1.3:
        score += 8
        rationale.append(f"The reclaim bar volume was {volume_ratio:.2f}x its recent same-feed median.")
    if close1 > close2:
        score += 6
    if distance > 2.0:
        risk_notes.append("Price is already more than 2% above computed session VWAP; a cleaner retest/hold may offer better invalidation structure.")

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
        },
        risk_notes=risk_notes,
        max_entry_extension_pct=3.0,
    )


def detect_known_setups(bars: list[dict[str, Any]]) -> dict[str, Any]:
    """Detect transparent, known intraday setup families from one same-feed bar series.

    These are deterministic TrendVisionAI implementations of commonly used setup
    concepts, not claims that a setup is universally profitable. Calibration is
    used later as a validator/filter rather than as the source of the strategy.
    """
    cleaned = _clean_bars(bars)
    matches: list[StrategyMatch] = []
    for detector in (
        lambda value: _opening_range_match(value, minutes=5),
        lambda value: _opening_range_match(value, minutes=15),
        _breakout_retest_match,
        _hod_breakout_match,
        _first_pullback_match,
        _vwap_reclaim_match,
    ):
        try:
            match = detector(cleaned)
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
        "policy": (
            "Known setup recognition is the primary strategy layer. Historical TrendVisionAI calibration validates/filters the setup; it does not invent the setup from scratch."
        ),
        "summary": (
            f"Primary setup: {primary.name} (score {primary.score}/100)."
            if primary is not None
            else "No configured known setup currently meets the deterministic candidate rules."
        ),
    }
