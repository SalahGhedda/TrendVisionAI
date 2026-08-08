from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from .models import CapturedNotification

# WinRT and UI Automation can expose slightly different whitespace/prefixes.
# Keep this deliberately tolerant while still requiring the TrendVision scanner
# header shape with a #channel inside parentheses.
_CHANNEL_RE = re.compile(
    r"TrendVision\s*\(\s*#(?P<channel>[^,\)]+)",
    re.IGNORECASE,
)
_TICKER_RE = re.compile(r"^\s*(?P<ticker>[A-Z][A-Z0-9.\-]{0,7})\b")

# Common scanner headings/labels that can appear before the real symbol. Keep
# these out of ticker detection when WinRT flattens a Discord embed into lines.
_TICKER_STOPWORDS = {
    "ALERT",
    "BORROW",
    "BREAKOUT",
    "CHECK",
    "FORM",
    "HALTED",
    "INITIAL",
    "MARKET",
    "MOMENTUM",
    "NEWS",
    "OFFERINGS",
    "POTENTIAL",
    "PRICE",
    "PUBLIC",
    "SEC",
    "SMALL",
    "SQUEEZE",
    "STOCK",
    "TRENDVISION",
    "WHALE",
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
        # Strip common invisible Unicode characters seen in notification payloads
        # before normal whitespace cleanup.
        value = (
            value.replace("\u200b", "")
            .replace("\u200e", "")
            .replace("\u200f", "")
            .replace("\ufeff", "")
        )
        value = " ".join(value.split()).strip()
        if value and value not in cleaned:
            cleaned.append(value)
    return cleaned


def _find_ticker(body_lines: list[str]) -> str | None:
    # Rich Discord embeds can put a heading before the symbol (for example
    # "Small Whale Alert" or "Initial Public Offerings Scanner"), so inspect a
    # few more lines than the original prototype did.
    for line in body_lines[:8]:
        match = _TICKER_RE.match(line)
        if not match:
            continue
        ticker = match.group("ticker").upper()
        if ticker not in _TICKER_STOPWORDS:
            return ticker
    return None


def parse_uia_texts(texts: list[str]) -> ParsedToast | None:
    """Parse a TrendVision Discord notification from normalized text lines.

    The function name is retained for compatibility with the original UIA
    prototype, but it now also accepts text extracted through WinRT's
    UserNotificationListener.
    """
    lines = _clean_lines(texts)
    if not lines:
        return None

    # The caller prepends the originating app name. Accept exact Discord or a
    # longer app label containing Discord, but still reject arbitrary windows.
    if not any("discord" in line.casefold() for line in lines[:3]):
        return None

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
    app_name = next(
        (line for line in lines[:3] if "discord" in line.casefold()),
        "Discord",
    )

    body_lines = lines[header_index + 1 :]
    body_lines = [
        line
        for line in body_lines
        if line.casefold() not in {"close", "dismiss", "x"}
    ]

    title: str | None = None
    body = ""
    if body_lines:
        title = body_lines[0]
        body = "\n".join(body_lines[1:]) if len(body_lines) > 1 else body_lines[0]

    ticker = _find_ticker(body_lines)
    raw_text = "\n".join(lines)
    normalized = "\n".join(line.casefold() for line in lines)
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
