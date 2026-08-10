from __future__ import annotations

import sqlite3
import time
from collections import defaultdict
from datetime import datetime
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QScrollArea, QVBoxLayout, QWidget

from . import desktop_ui_trade as trade


base = trade.stats.market.cal.ai.base
market = trade.stats.market


# Keep the launch-target optimization local to the desktop app. The listener
# remains the writer; these replacements only make dashboard reads cheaper.
def _fast_ticker_state(self: Any, ticker: str) -> dict[str, Any] | None:
    ticker = str(ticker or "").upper().strip()
    if not ticker or not self.database_path.exists():
        return None

    with self._connect() as connection:
        try:
            row = connection.execute(
                """
                SELECT ticker, first_seen_at, last_seen_at, event_count,
                       channel_count, channels_json, latest_event_type,
                       latest_headline, facts_json
                FROM ticker_states
                WHERE UPPER(ticker) = ?
                LIMIT 1
                """,
                (ticker,),
            ).fetchone()
        except sqlite3.OperationalError:
            return None

    if row is None:
        return None
    return {
        "ticker": row["ticker"],
        "first_seen_at": row["first_seen_at"],
        "last_seen_at": row["last_seen_at"],
        "event_count": row["event_count"],
        "channel_count": row["channel_count"],
        "channels": base._decode_json(row["channels_json"], []),
        "latest_event_type": row["latest_event_type"],
        "latest_headline": row["latest_headline"],
        "facts": base._decode_json(row["facts_json"], {}),
    }


def _fast_attention_list(
    self: Any,
    window_minutes: int,
    limit: int = 50,
) -> list[Any]:
    """Build attention from one recent-events query instead of N ticker queries."""
    if not self.database_path.exists():
        return []

    window = max(1, int(window_minutes))
    # A small safety margin lets Python's timezone-aware convergence filter make
    # the exact final cut while SQLite cheaply removes old history first.
    sqlite_window = window + 5
    with self._connect() as connection:
        try:
            rows = connection.execute(
                """
                SELECT received_at, channel, ticker, event_type, headline, data_json
                FROM scanner_events
                WHERE ticker IS NOT NULL
                  AND ticker <> ''
                  AND julianday(received_at) >= julianday('now', ?)
                ORDER BY received_at ASC, id ASC
                """,
                (f"-{sqlite_window} minutes",),
            ).fetchall()
        except sqlite3.OperationalError:
            return []

    grouped: dict[str, list[Any]] = defaultdict(list)
    for row in rows:
        ticker = str(row["ticker"] or "").upper().strip()
        if not ticker:
            continue
        grouped[ticker].append(
            base.TickerEventRecord(
                received_at=row["received_at"],
                channel=row["channel"],
                event_type=row["event_type"],
                headline=row["headline"],
                data=base._decode_json(row["data_json"], {}),
            )
        )

    reference_time = datetime.now().astimezone()
    results: list[Any] = []
    for ticker, events in grouped.items():
        summary = base.convergence_summary(
            ticker,
            events,
            window_minutes=window,
            reference_time=reference_time,
        )
        if summary["event_count"]:
            results.append(base.evaluate_attention(summary))

    results.sort(
        key=lambda item: (
            item.score,
            item.channel_count,
            item.event_count,
            item.ticker,
        ),
        reverse=True,
    )
    return results[: max(1, int(limit))]


base.DashboardRepository.ticker_state = _fast_ticker_state
base.DashboardRepository.attention_list = _fast_attention_list


# Automatic outcome classification does not need to rescan hundreds of rows
# twice every 15-second Alpaca poll. Completed horizons are minute-scale, so a
# 30-second local classification cadence keeps the UI much smoother.
_original_outcome_refresh = market.MarketTrackerController._refresh_automatic_outcomes


def _throttled_outcome_refresh(self: Any) -> dict[str, int]:
    now = time.monotonic()
    last = float(getattr(self, "_smooth_last_outcome_refresh", 0.0) or 0.0)
    if now - last < 30.0:
        return {"session_changes": 0, "review_changes": 0}
    self._smooth_last_outcome_refresh = now
    return _original_outcome_refresh(self)


market.MarketTrackerController._refresh_automatic_outcomes = _throttled_outcome_refresh


class SmoothTickerMemoryPage(trade.TradeTickerMemoryPage):
    """Ticker Memory with a real vertical scroll surface and lighter refreshes."""

    def __init__(self, repo: Any) -> None:
        self._smooth_last_refresh = 0.0
        self._smooth_last_ticker = ""
        self._smooth_last_trade_followup = 0.0
        super().__init__(repo)
        self._install_scroll_surface()

    def _install_scroll_surface(self) -> None:
        root = self.layout()
        if root is None or getattr(self, "_smooth_scroll", None) is not None:
            return

        # The inherited page grew over time (memory + AI + calibration + chart
        # experiment), but the original layout was never made scrollable. Move
        # the already-built top-level items into one scrollable content widget.
        items = []
        while root.count():
            items.append(root.takeAt(0))

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(14)
        content_layout.setAlignment(Qt.AlignTop)

        for item in items:
            widget = item.widget()
            child_layout = item.layout()
            if widget is not None:
                content_layout.addWidget(widget)
            elif child_layout is not None:
                content_layout.addLayout(child_layout)

        self.facts.setMinimumHeight(175)
        self.timeline.setMinimumHeight(175)
        self.review_text.setMinimumHeight(135)
        self.trade_text.setMinimumHeight(120)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(content)

        root.setSpacing(0)
        root.addWidget(scroll)
        self._smooth_scroll = scroll
        self._smooth_scroll_content = content

    def refresh(self) -> None:
        now = time.monotonic()
        ticker = str(getattr(self, "current_ticker", "") or "")
        ticker_changed = ticker != self._smooth_last_ticker
        if not ticker_changed and now - self._smooth_last_refresh < 3.0:
            return
        self._smooth_last_refresh = now
        self._smooth_last_ticker = ticker
        super().refresh()

    def _refresh_trade_followup(self) -> None:
        now = time.monotonic()
        last = float(getattr(self, "_smooth_last_trade_followup", 0.0) or 0.0)
        if now - last < 8.0:
            return
        self._smooth_last_trade_followup = now
        super()._refresh_trade_followup()


base.TickerMemoryPage = SmoothTickerMemoryPage


class SmoothTradeMainWindow(trade.TradeMainWindow):
    def __init__(self) -> None:
        super().__init__()

        # A 2-second full-page redraw is unnecessary for data that arrives on a
        # 15-second market cadence. Four seconds still feels live while avoiding
        # constant table reconstruction.
        self.refresh_timer.setInterval(4000)

        # Keep the listener log bounded. A QTextEdit with an ever-growing alert
        # history becomes progressively slower over a long session.
        try:
            self.system_page.log.document().setMaximumBlockCount(1500)
        except Exception:
            pass

        # Market polling used to rebuild off-screen Market Tracking and
        # Calibration pages every 15 seconds. The normal visible-page timer is
        # enough; only the page the user can actually see should redraw here.
        for slot in (self.market_page.refresh, self.calibration_page.refresh):
            try:
                self.market_controller.data_updated.disconnect(slot)
            except (TypeError, RuntimeError):
                pass

    def _read_listener_output(self) -> None:
        # The base window refreshed the entire visible page on every listener
        # stdout chunk. During alert bursts that can mean many full redraws per
        # second. Log the output and let the regular timer perform one refresh.
        data = bytes(self.listener_process.readAllStandardOutput()).decode(
            "utf-8", errors="replace"
        )
        self.system_page.append_log(data)

    def _refresh_trade_evaluations(self) -> None:
        # TradeMainWindow connected this virtual method to market data updates.
        # Use that single callback to refresh only the visible market-dependent
        # page instead of doing hidden work in three different pages.
        widget = self.stack.currentWidget()
        try:
            if widget is self.market_page:
                self.market_page.refresh()
            elif widget is self.calibration_page:
                self.calibration_page.refresh()
            elif widget is self.trade_plans_page:
                self.trade_plans_page.refresh()
        except Exception:
            pass


base.MainWindow = SmoothTradeMainWindow


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
