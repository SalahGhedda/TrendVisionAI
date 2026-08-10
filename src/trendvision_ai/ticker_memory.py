from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable


@dataclass(slots=True)
class TickerEventRecord:
    received_at: str
    channel: str
    event_type: str
    headline: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TickerState:
    ticker: str
    first_seen_at: str
    last_seen_at: str
    event_count: int
    channel_count: int
    channels: list[str]
    latest_event_type: str
    latest_headline: str
    facts: dict[str, dict[str, Any]]


def _parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _sort_key(event: TickerEventRecord) -> tuple[int, float | str]:
    parsed = _parse_time(event.received_at)
    if parsed is not None:
        try:
            return (0, parsed.timestamp())
        except (OverflowError, OSError, ValueError):
            pass
    return (1, event.received_at)


def _sorted_events(events: Iterable[TickerEventRecord]) -> list[TickerEventRecord]:
    return sorted(list(events), key=_sort_key)


def build_ticker_state(ticker: str, events: Iterable[TickerEventRecord]) -> TickerState | None:
    """Build durable per-ticker memory from every stored scanner event.

    Facts are merged chronologically. A newer non-empty value replaces an older
    one while retaining where and when the value came from. Missing fields never
    erase previously observed information.
    """
    ordered = _sorted_events(events)
    if not ordered:
        return None

    facts: dict[str, dict[str, Any]] = {}
    channels: list[str] = []

    for event in ordered:
        if event.channel not in channels:
            channels.append(event.channel)

        for key, value in event.data.items():
            if key == "ticker" or value is None or value == "" or value == []:
                continue
            facts[key] = {
                "value": value,
                "source_channel": event.channel,
                "received_at": event.received_at,
            }

    latest = ordered[-1]
    return TickerState(
        ticker=ticker.upper(),
        first_seen_at=ordered[0].received_at,
        last_seen_at=latest.received_at,
        event_count=len(ordered),
        channel_count=len(channels),
        channels=channels,
        latest_event_type=latest.event_type,
        latest_headline=latest.headline,
        facts=facts,
    )


def recent_events(
    events: Iterable[TickerEventRecord],
    *,
    window_minutes: int = 30,
    reference_time: datetime | None = None,
) -> list[TickerEventRecord]:
    """Return events inside a recent convergence window.

    This is intentionally not a trade score. It simply lets the system answer
    questions such as "how many independent scanner channels mentioned LRHC in
    the last 30 minutes?" without mixing in old alerts from hours or days ago.
    """
    ordered = _sorted_events(events)
    if not ordered:
        return []

    parsed_times = [
        value for value in (_parse_time(event.received_at) for event in ordered)
        if value is not None
    ]

    if reference_time is None:
        if not parsed_times:
            return ordered
        latest = max(parsed_times, key=lambda value: value.timestamp())
        reference_time = datetime.now(latest.tzinfo) if latest.tzinfo is not None else datetime.now()

    cutoff = reference_time - timedelta(minutes=max(1, window_minutes))
    result: list[TickerEventRecord] = []
    for event in ordered:
        event_time = _parse_time(event.received_at)
        if event_time is None:
            continue
        try:
            if event_time >= cutoff:
                result.append(event)
        except TypeError:
            # If legacy data mixed timezone-aware and naive timestamps, compare
            # them after dropping timezone information rather than crashing.
            if event_time.replace(tzinfo=None) >= cutoff.replace(tzinfo=None):
                result.append(event)
    return result


def convergence_summary(
    ticker: str,
    events: Iterable[TickerEventRecord],
    *,
    window_minutes: int = 30,
    reference_time: datetime | None = None,
) -> dict[str, Any]:
    recent = recent_events(
        events,
        window_minutes=window_minutes,
        reference_time=reference_time,
    )
    channels: list[str] = []
    for event in recent:
        if event.channel not in channels:
            channels.append(event.channel)

    return {
        "ticker": ticker.upper(),
        "window_minutes": window_minutes,
        "event_count": len(recent),
        "channel_count": len(channels),
        "channels": channels,
        "events": [
            {
                "received_at": event.received_at,
                "channel": event.channel,
                "event_type": event.event_type,
                "headline": event.headline,
                "data": event.data,
            }
            for event in recent
        ],
    }
