from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from .models import CapturedNotification

_CHANNEL_RE = re.compile(r"^TrendVision\s*\(#(?P<channel>[^,\)]+)", re.IGNORECASE)
_TICKER_RE = re.compile(r"^\s*(?P<ticker>[A-Z][A-Z0-9.\-]{0,7})\b")

_TICKER_STOPWORDS = {
    "ALERT",
    "CHECK",
    "FORM",
    "MARKET",
    "NEWS",
    "PRICE",
    "SEC",
    "SMALL",
    "STOCK",
    "TRENDVISION",
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


def _clean_lines(lines: list[str]) -> list[str]:
    cleaned: list[str] = []
    for value in lines:
        value = " ".join(value.replace("\u200b", "").split()).strip()
        if value and value not in cleaned:
            cleaned.append(value)
    return cleaned


def _find_ticker(body_lines: list[str]) -> str | None:
    for line in body_lines[:4]:
        match = _TICKER_RE.match(line)
        if not match:
            continue
        ticker = match.group("ticker").upper()
        if ticker not in _TICKER_STOPWORDS:
            return ticker
    return None


def parse_uia_texts(texts: list[str]) -> ParsedToast | None:
    lines = _clean_lines(texts)
    if not lines:
        return None

    # A real Discord Windows toast exposes the application name as its own UIA
    # text element. Requiring it prevents ordinary windows (VS Code, browser,
    # terminals, etc.) that merely contain the word "TrendVision" from being
    # mistaken for notifications.
    if not any(line.casefold() == "discord" for line in lines):
        return None

    # Do not match arbitrary text containing TrendVision/TrendVisionAI. The
    # notification header observed on Windows is shaped like:
    #   TrendVision (#volume-scanner, Trend Vision Scanner)
    header_index: int | None = None
    channel_match = None
    for i, line in enumerate(lines):
        match = _CHANNEL_RE.search(line)
        if match:
            header_index = i
            channel_match = match
            break

    if header_index is None or channel_match is None:
        return None

    channel = channel_match.group("channel").strip()
    app_name = "Discord"

    body_lines = lines[header_index + 1 :]
    body_lines = [line for line in body_lines if line.lower() not in {"close", "dismiss", "x"}]

    title: str | None = None
    body = ""
    if body_lines:
        title = body_lines[0]
        body = "\n".join(body_lines[1:]) if len(body_lines) > 1 else body_lines[0]

    ticker = _find_ticker(body_lines)
    raw_text = "\n".join(lines)
    normalized = "\n".join(line.lower() for line in lines)
    fingerprint = hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    return ParsedToast(
        app_name=app_name,
        source="TrendVision",
        channel=channel,
        title=title,
        body=body,
        raw_text=raw_text,
        ticker=ticker,
        fingerprint=fingerprint,
    )
