from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass

from .models import CapturedNotification

_CHANNEL_TOKEN_RE = re.compile(r"#(?P<channel>[A-Za-z0-9_-]+)", re.IGNORECASE)
_TICKER_RE = re.compile(r"^\s*(?P<ticker>[A-Z][A-Z0-9.\-]{0,7})\b")

_TICKER_STOPWORDS = {
    "ALERT", "BORROW", "BREAKOUT", "CHECK", "FORM", "HALTED", "INITIAL",
    "MARKET", "MOMENTUM", "NEW", "NEWS", "OFFERINGS", "POTENTIAL", "PRICE",
    "PUBLIC", "SEC", "SMALL", "SQUEEZE", "STOCK", "TRENDVISION", "WHALE",
}


@dataclass(slots=True)
class ParsedToast:
    app_name: str
    source: str
    channel: str | None
    title: str | None
    body: str
    raw_text: str
    ticker: str | None
    fingerprint: str

    def to_notification(self) -> CapturedNotification:
        return CapturedNotification.now(
            app_name=self.app_name,
            source=self.source,
            channel=self.channel,
            title=self.title,
            body=self.body,
            raw_text=self.raw_text,
            ticker=self.ticker,
            fingerprint=self.fingerprint,
        )


def _strip_format_chars(value: str) -> str:
    return "".join(ch for ch in value if unicodedata.category(ch) != "Cf")


def _normalize_line(value: str) -> str:
    value = _strip_format_chars(str(value))
    return " ".join(value.split()).strip()


def extract_channel_from_header(line: str) -> str | None:
    normalized = _normalize_line(line)
    if "trendvision" not in normalized.casefold():
        return None
    match = _CHANNEL_TOKEN_RE.search(normalized)
    if match is None:
        return None
    return match.group("channel").strip()


def _clean_lines(lines: list[str]) -> list[str]:
    cleaned: list[str] = []
    for value in lines:
        value = _normalize_line(value)
        if value and value not in cleaned:
            cleaned.append(value)
    return cleaned


def _find_ticker(body_lines: list[str]) -> str | None:
    for line in body_lines[:10]:
        match = _TICKER_RE.match(line)
        if not match:
            continue
        ticker = match.group("ticker").upper()
        if ticker not in _TICKER_STOPWORDS:
            return ticker
    return None


def parse_uia_texts(texts: list[str]) -> ParsedToast | None:
    """Normalize a TrendVision Discord notification from WinRT/UIA text lines.

    This transport layer preserves the payload and channel without pretending
    all channels share one title/ticker/body schema. Channel-specific meaning
    is handled by ``scanner_events.py``.
    """
    lines = _clean_lines(texts)
    if not lines:
        return None

    if not any("discord" in line.casefold() for line in lines[:3]):
        return None

    header_index: int | None = None
    channel: str | None = None
    for i, line in enumerate(lines):
        candidate = extract_channel_from_header(line)
        if candidate:
            header_index = i
            channel = candidate
            break

    if header_index is None or channel is None:
        return None

    app_name = next(
        (line for line in lines[:3] if "discord" in line.casefold()),
        "Discord",
    )

    body_lines = [
        line
        for line in lines[header_index + 1 :]
        if line.casefold() not in {"close", "dismiss", "x"}
    ]

    body = "\n".join(body_lines)
    channel_key = channel.casefold()

    # These notifications do not have one meaningful ticker at the transport
    # level: social-news has none, all-in-one can contain several, and the IPO
    # card must use the explicit Symbol field rather than guessing from text.
    no_single_ticker_channels = {"social-news", "all-in-one-scanner", "ipo-scanner"}
    ticker = None if channel_key in no_single_ticker_channels else _find_ticker(body_lines)

    raw_text = "\n".join(lines)
    normalized = "\n".join(line.casefold() for line in lines)
    fingerprint = hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    return ParsedToast(
        app_name=app_name,
        source="TrendVision",
        channel=channel,
        title=None,
        body=body,
        raw_text=raw_text,
        ticker=ticker,
        fingerprint=fingerprint,
    )
