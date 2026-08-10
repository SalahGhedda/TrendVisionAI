from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen

from .market_data import ALPACA_DATA_URL, DEFAULT_FEED


AUTO_CHART_LOOKBACK_MINUTES = 60
AUTO_CHART_LIMIT = 60
MIN_AUTO_CHART_BARS = 5


class AutoChartError(RuntimeError):
    pass


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_bars_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get("bars") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        return []
    bars: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        o = _num(item.get("o"))
        h = _num(item.get("h"))
        l = _num(item.get("l"))
        c = _num(item.get("c"))
        if any(value is None or value <= 0 for value in (o, h, l, c)):
            continue
        bars.append(
            {
                "timestamp": item.get("t"),
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": _num(item.get("v")),
                "vwap": _num(item.get("vw")),
                "trade_count": _num(item.get("n")),
            }
        )
    return bars


class AlpacaRecentBarsClient:
    """Fetch recent 1-minute bars for automatic chart context only."""

    def __init__(self, key_id: str, secret: str, *, feed: str = DEFAULT_FEED) -> None:
        self.key_id = key_id.strip()
        self.secret = secret.strip()
        self.feed = (feed or DEFAULT_FEED).strip().lower()

    def fetch_recent_bars(
        self,
        symbol: str,
        *,
        lookback_minutes: int = AUTO_CHART_LOOKBACK_MINUTES,
        limit: int = AUTO_CHART_LIMIT,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        ticker = symbol.upper().strip()
        if not ticker:
            return []
        clock = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        start = clock - timedelta(minutes=max(5, int(lookback_minutes)))
        query = urllib.parse.urlencode(
            {
                "timeframe": "1Min",
                "start": start.isoformat().replace("+00:00", "Z"),
                "end": clock.isoformat().replace("+00:00", "Z"),
                "limit": max(5, min(1000, int(limit))),
                "adjustment": "raw",
                "feed": self.feed,
                "sort": "asc",
            }
        )
        safe_ticker = urllib.parse.quote(ticker, safe="")
        request = urllib.request.Request(
            f"{ALPACA_DATA_URL}/v2/stocks/{safe_ticker}/bars?{query}",
            headers={
                "APCA-API-KEY-ID": self.key_id,
                "APCA-API-SECRET-KEY": self.secret,
                "Accept": "application/json",
                "User-Agent": "TrendVisionAI/auto-chart",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="replace")
            except Exception:
                detail = str(exc)
            raise AutoChartError(f"Alpaca historical-bars HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise AutoChartError(f"Alpaca historical-bars connection error: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise AutoChartError("Alpaca historical-bars response was invalid JSON.") from exc

        bars = normalize_bars_payload(payload if isinstance(payload, dict) else {})
        return bars[-max(5, int(limit)) :]


def _price_y(price: float, low: float, high: float, top: float, height: float) -> float:
    if high <= low:
        return top + height / 2.0
    return top + ((high - price) / (high - low)) * height


def render_candles_png(
    *,
    ticker: str,
    bars: list[dict[str, Any]],
    destination: str | Path,
    feed: str,
) -> Path:
    """Render a neutral chart image from Alpaca bars, without trade suggestions."""
    if len(bars) < MIN_AUTO_CHART_BARS:
        raise AutoChartError(
            f"Only {len(bars)} recent 1-minute bar(s) are available; at least {MIN_AUTO_CHART_BARS} are required for automatic chart context."
        )

    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    width, height = 1100, 650
    image = QImage(width, height, QImage.Format_ARGB32)
    image.fill(QColor("#0b1020"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)

    left, right, top = 72.0, 28.0, 58.0
    price_height = 430.0
    volume_top = 515.0
    volume_height = 92.0
    chart_width = width - left - right

    highs = [float(bar["high"]) for bar in bars]
    lows = [float(bar["low"]) for bar in bars]
    p_low = min(lows)
    p_high = max(highs)
    span = max(1e-9, p_high - p_low)
    p_low -= span * 0.05
    p_high += span * 0.05

    painter.setPen(QPen(QColor("#263047"), 1))
    for i in range(6):
        y = top + price_height * i / 5.0
        painter.drawLine(int(left), int(y), int(left + chart_width), int(y))
        price = p_high - (p_high - p_low) * i / 5.0
        painter.setPen(QPen(QColor("#8f9bb3"), 1))
        painter.drawText(8, int(y + 5), f"{price:.4f}")
        painter.setPen(QPen(QColor("#263047"), 1))

    count = len(bars)
    slot = chart_width / max(1, count)
    body_width = max(3.0, min(14.0, slot * 0.58))
    max_volume = max(float(bar.get("volume") or 0.0) for bar in bars) or 1.0

    for index, bar in enumerate(bars):
        x = left + slot * (index + 0.5)
        o = float(bar["open"])
        h = float(bar["high"])
        l = float(bar["low"])
        c = float(bar["close"])
        up = c >= o
        color = QColor("#23c483" if up else "#ef5b67")
        painter.setPen(QPen(color, 2))
        painter.drawLine(
            int(x),
            int(_price_y(h, p_low, p_high, top, price_height)),
            int(x),
            int(_price_y(l, p_low, p_high, top, price_height)),
        )
        y_open = _price_y(o, p_low, p_high, top, price_height)
        y_close = _price_y(c, p_low, p_high, top, price_height)
        body_top = min(y_open, y_close)
        body_height = max(2.0, abs(y_close - y_open))
        painter.fillRect(
            QRectF(x - body_width / 2.0, body_top, body_width, body_height),
            color,
        )

        volume = float(bar.get("volume") or 0.0)
        v_height = volume_height * min(1.0, volume / max_volume)
        painter.fillRect(
            QRectF(x - body_width / 2.0, volume_top + volume_height - v_height, body_width, v_height),
            QColor(color.red(), color.green(), color.blue(), 150),
        )

    painter.setPen(QColor("#f3f6fb"))
    painter.setFont(QFont("Segoe UI", 16, QFont.Bold))
    painter.drawText(int(left), 30, f"{ticker.upper()} — automatic 1-minute chart context")
    painter.setPen(QColor("#8f9bb3"))
    painter.setFont(QFont("Segoe UI", 9))
    painter.drawText(
        int(left),
        48,
        f"Source: Alpaca {feed.upper()} historical 1-minute bars • no BUY/SELL/SL/TP overlay • partial-venue when feed=IEX",
    )
    painter.drawText(int(left), 628, f"Bars shown: {len(bars)}")
    painter.end()

    if not image.save(str(path), "PNG"):
        raise AutoChartError(f"Could not save automatic chart image: {path}")
    return path
