from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from .models import CapturedNotification


# Canonical channel contracts based on the TrendVision formats supplied by the
# user. Fields may be absent in a Windows toast because Discord notifications
# can truncate rich embeds; raw_text is always retained so later enrichment can
# fill the gaps without changing the semantic model.
CHANNEL_SCHEMAS: dict[str, tuple[str, ...]] = {
    "social-news": ("body",),
    "news-scanner": ("ticker", "headline", "price", "market_cap", "timestamp", "link", "keywords"),
    "volume-scanner": (
        "ticker", "alert_number", "change_pct", "price", "market_cap", "float",
        "min_volume", "relative_volume", "monetary_volume", "sec_filings", "alert_reason",
    ),
    "whale-scanner": (
        "ticker", "direction", "price", "shares", "order_value",
        "float_value", "float_percent", "market_cap_value", "market_cap_percent",
    ),
    "potential-squeeze-alerts": (
        "ticker", "alert_number", "change_pct", "price", "market_cap", "float",
        "min_volume", "relative_volume", "monetary_volume", "sec_filings",
        "zero_borrow", "alert_reason",
    ),
    "0-borrow-scanner": ("ticker", "no_shares_available", "market_cap", "ctb_fee_pct", "short_interest_pct"),
    "halt-scanner": ("ticker", "halt_status", "price", "change_pct", "volume", "reason"),
    "ipo-scanner": (
        "ticker", "status", "exchange", "list_date", "name", "price_range",
        "issued_shares", "updated_fields",
    ),
    "all-in-one-scanner": (
        "ticker", "rank", "signal", "change_pct", "price", "float", "market_cap",
        "relative_volume", "one_min_volume", "zero_borrow", "ctb_fee_pct",
        "short_interest_pct", "sec_filings",
    ),
}

_EVENT_TYPES = {
    "news-scanner": "ticker_news",
    "social-news": "market_news",
    "volume-scanner": "volume",
    "whale-scanner": "whale",
    "potential-squeeze-alerts": "potential_squeeze",
    "0-borrow-scanner": "zero_borrow",
    "halt-scanner": "halt",
    "ipo-scanner": "ipo",
    "all-in-one-scanner": "all_in_one",
}

_FLAG_TOKEN_RE = re.compile(r":flag_[a-z]{2,3}:", re.IGNORECASE)
_TICKER_START_RE = re.compile(r"^\s*([A-Z][A-Z0-9.\-]{0,7})\b")
_FORM_RE = re.compile(r"\bFORM\s+[A-Z0-9][A-Z0-9\-/]*", re.IGNORECASE)


@dataclass(slots=True)
class ScannerEvent:
    received_at: str
    channel: str
    ticker: str | None
    event_type: str
    headline: str
    raw_text: str
    fingerprint: str
    data: dict[str, Any] = field(default_factory=dict)
    item_index: int = 0


def _lines(notification: CapturedNotification) -> list[str]:
    return [line.strip() for line in notification.body.splitlines() if line.strip()]


def _joined(lines: list[str]) -> str:
    return " | ".join(lines)


def _ticker_from_line(line: str) -> str | None:
    match = _TICKER_START_RE.match(line)
    return match.group(1).upper() if match else None


def _strip_ticker_prefix(line: str, ticker: str | None) -> str:
    if not ticker:
        return line.strip()
    value = re.sub(rf"^\s*{re.escape(ticker)}\b", "", line, count=1)
    value = _FLAG_TOKEN_RE.sub("", value)
    value = re.sub(r"^[\s:|·•\-–—]+", "", value)
    return value.strip()


def _field(text: str, label: str) -> str | None:
    # Handles common flattened forms such as "Price $2.99", "MCap 33.66M",
    # "Rel Vol 10.33x", and "CTB Fee 258.04%".
    pattern = re.compile(
        rf"\b{re.escape(label)}\b\s*[:\-]?\s*\$?([0-9]+(?:\.[0-9]+)?(?:[KMB])?(?:x|%)?)",
        re.IGNORECASE,
    )
    match = pattern.search(text)
    return match.group(1) if match else None


def _alert_number(text: str) -> int | None:
    match = re.search(r"Alert\s*#(\d+)", text, re.IGNORECASE)
    return int(match.group(1)) if match else None


def _rank(text: str) -> int | None:
    match = re.search(r"#(\d+)", text)
    return int(match.group(1)) if match else None


def _change(text: str) -> float | None:
    match = re.search(r"([↑↓+\-])\s*([0-9]+(?:\.[0-9]+)?)%", text)
    if not match:
        return None
    value = float(match.group(2))
    return -value if match.group(1) in {"↓", "-"} else value


def _forms(text: str) -> list[str]:
    return list(dict.fromkeys(match.group(0).upper() for match in _FORM_RE.finditer(text)))


def _after_label(text: str, label: str) -> str | None:
    match = re.search(rf"\b{re.escape(label)}\b\s*[:\-]?\s*([^|\n]+)", text, re.IGNORECASE)
    return match.group(1).strip() if match else None


def _event(notification: CapturedNotification, *, ticker: str | None, summary: str,
           data: dict[str, Any], item_index: int = 0) -> ScannerEvent:
    channel = (notification.channel or "").strip().lower()
    event_type = _EVENT_TYPES.get(channel, channel.replace("-", "_"))
    normalized = "|".join([
        notification.received_at, channel, ticker or "", str(item_index), summary.casefold(), notification.fingerprint,
    ])
    fingerprint = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return ScannerEvent(
        received_at=notification.received_at,
        channel=channel,
        ticker=ticker,
        event_type=event_type,
        headline=summary,
        raw_text=notification.raw_text,
        fingerprint=fingerprint,
        data={key: value for key, value in data.items() if value is not None},
        item_index=item_index,
    )


def _parse_social(notification: CapturedNotification, lines: list[str]) -> list[ScannerEvent]:
    body = "\n".join(lines).strip()
    return [_event(notification, ticker=None, summary=body, data={"body": body})] if body else []


def _parse_news(notification: CapturedNotification, lines: list[str]) -> list[ScannerEvent]:
    if not lines:
        return []
    ticker = notification.ticker or _ticker_from_line(lines[0])
    headline = _strip_ticker_prefix(lines[0], ticker)
    text = _joined(lines)
    data = {
        "ticker": ticker,
        "headline": headline,
        "price": _field(text, "Price"),
        "market_cap": _field(text, "MCap"),
        "timestamp": _after_label(text, "Timestamp"),
        "link": "View News" if "view news" in text.casefold() else None,
        "keywords": _after_label(text, "Keywords"),
    }
    return [_event(notification, ticker=ticker, summary=headline or lines[0], data=data)]


def _parse_volume_like(notification: CapturedNotification, lines: list[str], *, squeeze: bool) -> list[ScannerEvent]:
    if not lines:
        return []
    text = _joined(lines)
    ticker = notification.ticker
    if not ticker:
        for line in lines:
            candidate = _ticker_from_line(line)
            if candidate and candidate not in {"POTENTIAL", "TRENDVISION"}:
                ticker = candidate
                break
    data = {
        "ticker": ticker,
        "alert_number": _alert_number(text),
        "change_pct": _change(text),
        "price": _field(text, "Price"),
        "market_cap": _field(text, "MCap"),
        "float": _field(text, "Float"),
        "min_volume": _field(text, "Min Vol"),
        "relative_volume": _field(text, "Rel Vol"),
        "monetary_volume": _field(text, "Mon Vol"),
        "sec_filings": _forms(text),
        "alert_reason": _after_label(text, "Alert Reason"),
    }
    if squeeze:
        data["zero_borrow"] = "0 borrow" in text.casefold() or "no shares available to borrow" in text.casefold()
    summary = next((line for line in lines if ticker and line.startswith(ticker)), lines[0])
    return [_event(notification, ticker=ticker, summary=summary, data=data)]


def _parse_whale(notification: CapturedNotification, lines: list[str]) -> list[ScannerEvent]:
    text = _joined(lines)
    ticker = notification.ticker
    if not ticker:
        for line in lines:
            candidate = _ticker_from_line(line)
            if candidate and candidate not in {"SMALL", "TRENDVISION"}:
                ticker = candidate
                break
    direction = None
    if "price is dropping" in text.casefold():
        direction = "down"
    elif "price is increasing" in text.casefold():
        direction = "up"

    float_match = re.search(r"Float\s*:\s*([^()|]+)\s*\(([0-9.]+)%\)", text, re.IGNORECASE)
    mcap_match = re.search(r"Market\s*Cap\s*:\s*([^()|]+)\s*\(([0-9.]+)%\)", text, re.IGNORECASE)
    data = {
        "ticker": ticker,
        "direction": direction,
        "price": _field(text, "Price"),
        "shares": _field(text, "Shares"),
        "order_value": _field(text, "Order Value"),
        "float_value": float_match.group(1).strip() if float_match else None,
        "float_percent": float_match.group(2) if float_match else None,
        "market_cap_value": mcap_match.group(1).strip() if mcap_match else None,
        "market_cap_percent": mcap_match.group(2) if mcap_match else None,
    }
    summary = f"{ticker or '?'} small whale {direction or 'alert'}"
    return [_event(notification, ticker=ticker, summary=summary, data=data)]


def _parse_zero_borrow(notification: CapturedNotification, lines: list[str]) -> list[ScannerEvent]:
    text = _joined(lines)
    ticker = notification.ticker
    if not ticker:
        for line in lines:
            candidate = _ticker_from_line(line)
            if candidate and candidate != "TRENDVISION":
                ticker = candidate
                break
    data = {
        "ticker": ticker,
        "no_shares_available": "no shares available to borrow" in text.casefold(),
        "market_cap": _field(text, "Market Cap"),
        "ctb_fee_pct": _field(text, "CTB Fee"),
        "short_interest_pct": _field(text, "Short Int"),
    }
    return [_event(notification, ticker=ticker, summary=f"{ticker or '?'} zero borrow", data=data)]


def _parse_halt(notification: CapturedNotification, lines: list[str]) -> list[ScannerEvent]:
    text = _joined(lines)
    ticker = notification.ticker
    ticker_line = next((line for line in lines if ticker and line.startswith(ticker)), "")
    if not ticker:
        for line in lines:
            candidate = _ticker_from_line(line)
            if candidate and candidate != "TRENDVISION":
                ticker = candidate
                ticker_line = line
                break
    status_match = re.search(r"\((HALTED(?:\s+(?:UP|DOWN))?)\)", ticker_line or text, re.IGNORECASE)
    data = {
        "ticker": ticker,
        "halt_status": status_match.group(1).upper() if status_match else "HALTED",
        "price": _field(text, "Price"),
        "change_pct": _change(text),
        "volume": _field(text, "Volume"),
        "reason": _after_label(text, "Reason"),
    }
    return [_event(notification, ticker=ticker, summary=ticker_line or f"{ticker or '?'} halted", data=data)]


def _parse_ipo(notification: CapturedNotification, lines: list[str]) -> list[ScannerEvent]:
    text = _joined(lines)
    # Prefer an explicit Symbol/Exchange/List Date row over the generic ticker
    # guess; this prevents headings like "New upcoming IPO" from becoming NEW.
    row = re.search(r"\b([A-Z][A-Z0-9.\-]{0,7})\s+(NASDAQ|NYSE|AMEX)\s+(\d{4}-\d{2}-\d{2})\b", text)
    ticker = row.group(1) if row else notification.ticker
    exchange = row.group(2) if row else _after_label(text, "Exchange")
    list_date = row.group(3) if row else _after_label(text, "List Date")
    status = "updated" if "info updated" in text.casefold() else "new"
    updated_fields = []
    if "updated fields" in text.casefold():
        marker = next((i for i, line in enumerate(lines) if "updated fields" in line.casefold()), None)
        if marker is not None:
            updated_fields = lines[marker + 1 :]
    data = {
        "ticker": ticker,
        "status": status,
        "exchange": exchange,
        "list_date": list_date,
        "name": _after_label(text, "Name"),
        "price_range": _after_label(text, "Price Range"),
        "issued_shares": _after_label(text, "Issued Shares"),
        "updated_fields": updated_fields,
    }
    return [_event(notification, ticker=ticker, summary=f"IPO {status}: {ticker or '?'}", data=data)]


def _parse_all_in_one(notification: CapturedNotification, lines: list[str]) -> list[ScannerEvent]:
    events: list[ScannerEvent] = []
    current_data: dict[str, Any] | None = None
    current_line = ""
    current_ticker: str | None = None

    def flush() -> None:
        nonlocal current_data, current_line, current_ticker
        if current_data is None:
            return
        events.append(_event(
            notification,
            ticker=current_ticker,
            summary=current_line,
            data=current_data,
            item_index=len(events),
        ))
        current_data = None
        current_line = ""
        current_ticker = None

    for line in lines:
        ticker = _ticker_from_line(line)
        # Alert item lines contain a rank (#n), unlike SEC/borrow continuation
        # lines. This lets one Discord notification yield multiple ticker events.
        if ticker and re.search(r"#\d+", line):
            flush()
            current_ticker = ticker
            current_line = line
            signal_match = re.search(r"#\d+\s*[·|•:]?\s*([A-Z][A-Z0-9 ]{1,16})\s*[·|•:]", line)
            current_data = {
                "ticker": ticker,
                "rank": _rank(line),
                "signal": signal_match.group(1).strip() if signal_match else None,
                "change_pct": _change(line),
                "price": re.search(r"\$([0-9]+(?:\.[0-9]+)?)", line).group(1) if re.search(r"\$([0-9]+(?:\.[0-9]+)?)", line) else None,
                "float": _field(line, "FT"),
                "market_cap": _field(line, "MC"),
                "relative_volume": _field(line, "RV"),
                "one_min_volume": _field(line, "1V"),
                "zero_borrow": False,
                "sec_filings": [],
            }
            continue

        if current_data is not None:
            low = line.casefold()
            current_data["sec_filings"] = list(dict.fromkeys(current_data.get("sec_filings", []) + _forms(line)))
            if "0 borrow" in low or "no shares available to borrow" in low:
                current_data["zero_borrow"] = True
            ctb = _field(line, "CTB")
            si = _field(line, "SI")
            if ctb is not None:
                current_data["ctb_fee_pct"] = ctb
            if si is not None:
                current_data["short_interest_pct"] = si

    flush()

    # If Windows only exposed one compressed line and it did not include #rank,
    # retain a generic all-in-one event rather than dropping the notification.
    if not events and lines:
        ticker = notification.ticker or _ticker_from_line(lines[0])
        events.append(_event(
            notification,
            ticker=ticker,
            summary=lines[0],
            data={"ticker": ticker, "raw_payload": "\n".join(lines)},
        ))
    return events


def build_scanner_events(notification: CapturedNotification) -> list[ScannerEvent]:
    """Create zero, one, or many semantically correct events for a channel."""
    channel = (notification.channel or "").strip().lower()
    if not channel:
        return []
    lines = _lines(notification)

    if channel == "social-news":
        return _parse_social(notification, lines)
    if channel == "news-scanner":
        return _parse_news(notification, lines)
    if channel == "volume-scanner":
        return _parse_volume_like(notification, lines, squeeze=False)
    if channel == "potential-squeeze-alerts":
        return _parse_volume_like(notification, lines, squeeze=True)
    if channel == "whale-scanner":
        return _parse_whale(notification, lines)
    if channel == "0-borrow-scanner":
        return _parse_zero_borrow(notification, lines)
    if channel == "halt-scanner":
        return _parse_halt(notification, lines)
    if channel == "ipo-scanner":
        return _parse_ipo(notification, lines)
    if channel == "all-in-one-scanner":
        return _parse_all_in_one(notification, lines)

    # Unknown channels remain lossless but are not forced into another channel's
    # schema. We can add a contract once a real format is supplied.
    if not lines:
        return []
    return [_event(
        notification,
        ticker=notification.ticker,
        summary=lines[0],
        data={"raw_payload": "\n".join(lines)},
    )]


def build_scanner_event(notification: CapturedNotification) -> ScannerEvent | None:
    """Backward-compatible helper returning only the first event."""
    events = build_scanner_events(notification)
    return events[0] if events else None
