from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


CHANNEL_WEIGHTS = {
    "all-in-one-scanner": 3,
    "potential-squeeze-alerts": 3,
    "news-scanner": 2,
    "volume-scanner": 2,
    "0-borrow-scanner": 2,
    "whale-scanner": 1,
    "halt-scanner": 0,
    "ipo-scanner": 0,
}


@dataclass(slots=True)
class AttentionResult:
    ticker: str
    score: int
    tier: str
    event_count: int
    channel_count: int
    channels: list[str]
    reasons: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)


def _number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace("$", "").replace(",", "")
    multiplier = 1.0
    if text.lower().endswith("x"):
        text = text[:-1]
    if text.endswith("%"):
        text = text[:-1]
    try:
        return float(text) * multiplier
    except ValueError:
        return None


def evaluate_attention(summary: dict[str, Any]) -> AttentionResult:
    """Rank how much *review attention* a recent ticker cluster deserves.

    This is deliberately not a buy/sell score. It rewards independent scanner
    convergence and information-rich TrendVision signals so the user can focus
    on a small subset of tickers instead of reading every alert manually.
    """
    ticker = str(summary.get("ticker") or "?").upper()
    events = list(summary.get("events") or [])
    channels = list(summary.get("channels") or [])

    score = 0
    reasons: list[str] = []
    risk_flags: list[str] = []

    # Count each scanner channel once so repeated spam from one channel does not
    # dominate the ranking.
    for channel in channels:
        weight = CHANNEL_WEIGHTS.get(channel, 0)
        if weight:
            score += weight
            reasons.append(f"#{channel} (+{weight})")

    # Independent-channel convergence is more useful than repeated alerts from
    # only one source.
    if len(channels) >= 2:
        bonus = min(4, (len(channels) - 1) * 2)
        score += bonus
        reasons.append(f"{len(channels)} scanner channels converged (+{bonus})")

    # A small capped bonus recognizes repeated confirmation without allowing a
    # noisy scanner to overwhelm the ranking.
    repeat_count = max(0, len(events) - len(channels))
    if repeat_count:
        bonus = min(2, repeat_count)
        score += bonus
        reasons.append(f"{repeat_count} repeat confirmation(s) (+{bonus})")

    for event in events:
        data = event.get("data") or {}
        channel = str(event.get("channel") or "")

        signal = str(data.get("signal") or "").upper().strip()
        if signal in {"MOMENTUM", "BREAKOUT"}:
            score += 2
            reasons.append(f"{signal} signal (+2)")
        elif signal in {"REV V", "REV VOLUME", "REL V"}:
            score += 1
            reasons.append(f"{signal} signal (+1)")

        rv = _number(data.get("relative_volume"))
        if rv is not None:
            if rv >= 5:
                score += 2
                reasons.append(f"relative volume {rv:g}x (+2)")
            elif rv >= 2:
                score += 1
                reasons.append(f"relative volume {rv:g}x (+1)")

        if data.get("zero_borrow") is True or data.get("no_shares_available") is True:
            score += 1
            reasons.append("no shares available / zero borrow (+1)")

        direction = str(data.get("direction") or "").casefold()
        if channel == "whale-scanner" and direction == "up":
            score += 1
            reasons.append("whale alert while price increasing (+1)")
        elif channel == "whale-scanner" and direction == "down":
            risk_flags.append("whale alert says price is dropping")

        halt_status = data.get("halt_status")
        if halt_status or channel == "halt-scanner":
            risk_flags.append(f"halt event: {halt_status or 'HALTED'}")

    # Remove duplicate reason strings while preserving order.
    reasons = list(dict.fromkeys(reasons))
    risk_flags = list(dict.fromkeys(risk_flags))

    if score >= 10:
        tier = "HIGH ATTENTION"
    elif score >= 7:
        tier = "REVIEW"
    elif score >= 4:
        tier = "WATCH"
    else:
        tier = "LOW"

    return AttentionResult(
        ticker=ticker,
        score=score,
        tier=tier,
        event_count=len(events),
        channel_count=len(channels),
        channels=channels,
        reasons=reasons,
        risk_flags=risk_flags,
    )
