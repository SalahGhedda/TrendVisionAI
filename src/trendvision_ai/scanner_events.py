from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .models import CapturedNotification


@dataclass(slots=True)
class ScannerEvent:
    received_at: str
    channel: str
    ticker: str | None
    event_type: str
    headline: str
    raw_text: str
    fingerprint: str


def build_scanner_event(notification: CapturedNotification) -> ScannerEvent | None:
    """Turn a parsed TrendVision notification into a generic scanner event.

    This is deliberately generic for now. Windows notifications sometimes
    expose only the alert headline, not all fields from the Discord embed. We
    still want to persist a stable per-channel/per-ticker event now, then enrich
    it later when channel-specific parsers or market-data lookups are added.
    """
    channel = (notification.channel or "").strip().lower()
    if not channel:
        return None

    headline = (notification.title or notification.body or "").strip()
    if not headline:
        return None

    event_type = {
        "news-scanner": "ticker_news",
        "social-news": "market_news",
        "volume-scanner": "volume",
        "whale-scanner": "whale",
        "potential-squeeze-alerts": "potential_squeeze",
        "0-borrow-scanner": "zero_borrow",
        "halt-scanner": "halt",
        "ipo-scanner": "ipo",
        "all-in-one-scanner": "all_in_one",
    }.get(channel, channel.replace("-", "_"))

    normalized = "|".join(
        [
            notification.received_at,
            channel,
            notification.ticker or "",
            headline.casefold(),
            notification.fingerprint,
        ]
    )
    fingerprint = hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    return ScannerEvent(
        received_at=notification.received_at,
        channel=channel,
        ticker=notification.ticker,
        event_type=event_type,
        headline=headline,
        raw_text=notification.raw_text,
        fingerprint=fingerprint,
    )
