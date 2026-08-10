from __future__ import annotations

import json
import os
import sqlite3
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QProcess, QProcessEnvironment, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QFont
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .attention import AttentionResult, evaluate_attention
from .config import load_config
from .ticker_memory import TickerEventRecord, build_ticker_state, convergence_summary


APP_STYLE = """
QMainWindow, QWidget {
    background: #0f1117;
    color: #e8eaf0;
    font-family: "Segoe UI";
    font-size: 10pt;
}
QFrame#sidebar {
    background: #151821;
    border-right: 1px solid #262b38;
}
QLabel#brand {
    color: #ffffff;
    font-size: 19pt;
    font-weight: 700;
}
QLabel#brandAccent {
    color: #ef4444;
    font-size: 9pt;
    font-weight: 700;
}
QLabel#pageTitle {
    color: #ffffff;
    font-size: 22pt;
    font-weight: 700;
}
QLabel#muted {
    color: #8f98aa;
}
QLabel#metricValue {
    color: #ffffff;
    font-size: 18pt;
    font-weight: 700;
}
QFrame#card {
    background: #181c26;
    border: 1px solid #292f3d;
    border-radius: 10px;
}
QPushButton#nav {
    background: transparent;
    color: #aeb6c7;
    border: 0;
    border-radius: 8px;
    text-align: left;
    padding: 11px 14px;
    font-size: 10.5pt;
}
QPushButton#nav:hover {
    background: #202532;
    color: #ffffff;
}
QPushButton#nav:checked {
    background: #2a2024;
    color: #ff6b6b;
    border-left: 3px solid #ef4444;
    font-weight: 600;
}
QPushButton#primary {
    background: #ef4444;
    color: white;
    border: 0;
    border-radius: 7px;
    padding: 8px 14px;
    font-weight: 600;
}
QPushButton#primary:hover { background: #f05252; }
QPushButton#secondary {
    background: #202532;
    color: #e8eaf0;
    border: 1px solid #343b4b;
    border-radius: 7px;
    padding: 8px 14px;
}
QPushButton#secondary:hover { background: #292f3d; }
QLineEdit, QComboBox {
    background: #181c26;
    color: #e8eaf0;
    border: 1px solid #343b4b;
    border-radius: 7px;
    padding: 7px 9px;
    min-height: 20px;
}
QComboBox QAbstractItemView {
    background: #181c26;
    color: #e8eaf0;
    selection-background-color: #343b4b;
}
QTableWidget {
    background: #13161e;
    alternate-background-color: #171b24;
    border: 1px solid #292f3d;
    border-radius: 8px;
    gridline-color: #242a36;
    selection-background-color: #33252a;
    selection-color: #ffffff;
}
QHeaderView::section {
    background: #1d222d;
    color: #aeb6c7;
    border: 0;
    border-bottom: 1px solid #303746;
    padding: 8px;
    font-weight: 600;
}
QTextEdit {
    background: #0b0d12;
    color: #c8d0df;
    border: 1px solid #292f3d;
    border-radius: 8px;
    font-family: Consolas;
    font-size: 9pt;
}
"""


CHANNEL_LABELS = {
    "all-in-one-scanner": "All-in-One",
    "news-scanner": "News",
    "social-news": "Social News",
    "volume-scanner": "Volume",
    "whale-scanner": "Whale",
    "potential-squeeze-alerts": "Squeeze",
    "0-borrow-scanner": "0 Borrow",
    "halt-scanner": "Halt",
    "ipo-scanner": "IPO",
}


def _decode_json(value: str | None, fallback: Any) -> Any:
    try:
        decoded = json.loads(value or "")
        return decoded
    except (json.JSONDecodeError, TypeError):
        return fallback


def _display_time(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.strftime("%H:%M:%S")
    except (TypeError, ValueError):
        return str(value)


def _fact_value(facts: dict[str, Any], key: str) -> Any:
    entry = facts.get(key)
    if isinstance(entry, dict):
        return entry.get("value")
    return None


class DashboardRepository:
    """Read-only query layer used by the desktop UI.

    The listener remains the writer. SQLite handles concurrent reads while the
    listener appends scanner events and refreshes ticker state.
    """

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=2.0)
        connection.row_factory = sqlite3.Row
        return connection

    def counts(self) -> dict[str, int]:
        if not self.database_path.exists():
            return {"events": 0, "tickers": 0, "raw": 0}
        with self._connect() as connection:
            result = {}
            for key, table in (
                ("events", "scanner_events"),
                ("tickers", "ticker_states"),
                ("raw", "raw_notifications"),
            ):
                try:
                    result[key] = int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                except sqlite3.OperationalError:
                    result[key] = 0
            return result

    def list_ticker_states(self, limit: int = 500) -> list[dict[str, Any]]:
        if not self.database_path.exists():
            return []
        with self._connect() as connection:
            try:
                rows = connection.execute(
                    """
                    SELECT ticker, first_seen_at, last_seen_at, event_count,
                           channel_count, channels_json, latest_event_type,
                           latest_headline, facts_json
                    FROM ticker_states
                    ORDER BY last_seen_at DESC
                    LIMIT ?
                    """,
                    (max(1, limit),),
                ).fetchall()
            except sqlite3.OperationalError:
                return []
        return [
            {
                "ticker": row["ticker"],
                "first_seen_at": row["first_seen_at"],
                "last_seen_at": row["last_seen_at"],
                "event_count": row["event_count"],
                "channel_count": row["channel_count"],
                "channels": _decode_json(row["channels_json"], []),
                "latest_event_type": row["latest_event_type"],
                "latest_headline": row["latest_headline"],
                "facts": _decode_json(row["facts_json"], {}),
            }
            for row in rows
        ]

    def list_events(
        self,
        *,
        limit: int = 250,
        ticker: str | None = None,
        channel: str | None = None,
    ) -> list[dict[str, Any]]:
        if not self.database_path.exists():
            return []
        clauses: list[str] = []
        params: list[Any] = []
        if ticker:
            clauses.append("UPPER(ticker) = ?")
            params.append(ticker.upper())
        if channel:
            clauses.append("channel = ?")
            params.append(channel)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(max(1, limit))
        with self._connect() as connection:
            try:
                rows = connection.execute(
                    f"""
                    SELECT id, received_at, channel, ticker, event_type,
                           headline, data_json, item_index
                    FROM scanner_events
                    {where}
                    ORDER BY received_at DESC, id DESC
                    LIMIT ?
                    """,
                    params,
                ).fetchall()
            except sqlite3.OperationalError:
                return []
        return [
            {
                "id": row["id"],
                "received_at": row["received_at"],
                "channel": row["channel"],
                "ticker": row["ticker"],
                "event_type": row["event_type"],
                "headline": row["headline"],
                "data": _decode_json(row["data_json"], {}),
                "item_index": row["item_index"],
            }
            for row in rows
        ]

    def ticker_events(self, ticker: str) -> list[TickerEventRecord]:
        rows = self.list_events(limit=5000, ticker=ticker)
        rows.reverse()
        return [
            TickerEventRecord(
                received_at=row["received_at"],
                channel=row["channel"],
                event_type=row["event_type"],
                headline=row["headline"],
                data=row["data"],
            )
            for row in rows
        ]

    def ticker_state(self, ticker: str) -> dict[str, Any] | None:
        ticker = ticker.upper().strip()
        if not ticker:
            return None
        events = self.ticker_events(ticker)
        state = build_ticker_state(ticker, events)
        return asdict(state) if state is not None else None

    def convergence(self, ticker: str, window_minutes: int) -> dict[str, Any]:
        return convergence_summary(
            ticker,
            self.ticker_events(ticker),
            window_minutes=window_minutes,
        )

    def attention_list(self, window_minutes: int, limit: int = 50) -> list[AttentionResult]:
        results: list[AttentionResult] = []
        for state in self.list_ticker_states(limit=1000):
            summary = self.convergence(state["ticker"], window_minutes)
            if summary["event_count"] == 0:
                continue
            results.append(evaluate_attention(summary))
        results.sort(
            key=lambda item: (item.score, item.channel_count, item.event_count, item.ticker),
            reverse=True,
        )
        return results[:limit]


class MetricCard(QFrame):
    def __init__(self, title: str, value: str = "0") -> None:
        super().__init__()
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 13, 16, 13)
        title_label = QLabel(title)
        title_label.setObjectName("muted")
        self.value_label = QLabel(value)
        self.value_label.setObjectName("metricValue")
        layout.addWidget(title_label)
        layout.addWidget(self.value_label)

    def set_value(self, value: Any) -> None:
        self.value_label.setText(str(value))


class DashboardPage(QWidget):
    ticker_requested = Signal(str)

    def __init__(self, repo: DashboardRepository) -> None:
        super().__init__()
        self.repo = repo
        self.window_minutes = 30

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)

        top = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("Attention List")
        title.setObjectName("pageTitle")
        subtitle = QLabel("Recent scanner convergence ranked for review. This is not a buy/sell signal.")
        subtitle.setObjectName("muted")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        top.addLayout(title_box)
        top.addStretch()
        self.window_combo = QComboBox()
        self.window_combo.addItems(["15 min", "30 min", "60 min", "120 min"])
        self.window_combo.setCurrentText("30 min")
        self.window_combo.currentTextChanged.connect(self._window_changed)
        top.addWidget(QLabel("Window"))
        top.addWidget(self.window_combo)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.setObjectName("secondary")
        refresh_btn.clicked.connect(self.refresh)
        top.addWidget(refresh_btn)
        root.addLayout(top)

        metrics = QHBoxLayout()
        self.ticker_card = MetricCard("Tracked tickers")
        self.event_card = MetricCard("Scanner events")
        self.high_card = MetricCard("High attention")
        self.multi_card = MetricCard("Multi-channel now")
        for card in (self.ticker_card, self.event_card, self.high_card, self.multi_card):
            metrics.addWidget(card)
        root.addLayout(metrics)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["Tier", "Ticker", "Score", "Events", "Channels", "Signals", "Why"]
        )
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.Stretch)
        header.setSectionResizeMode(6, QHeaderView.Stretch)
        self.table.cellDoubleClicked.connect(self._open_ticker)
        root.addWidget(self.table, 1)

    def _window_changed(self, text: str) -> None:
        try:
            self.window_minutes = int(text.split()[0])
        except (ValueError, IndexError):
            self.window_minutes = 30
        self.refresh()

    def _open_ticker(self, row: int, _column: int) -> None:
        item = self.table.item(row, 1)
        if item:
            self.ticker_requested.emit(item.text())

    def refresh(self) -> None:
        counts = self.repo.counts()
        results = self.repo.attention_list(self.window_minutes, limit=75)
        self.ticker_card.set_value(counts["tickers"])
        self.event_card.set_value(counts["events"])
        self.high_card.set_value(sum(1 for item in results if item.tier == "HIGH ATTENTION"))
        self.multi_card.set_value(sum(1 for item in results if item.channel_count >= 2))

        self.table.setRowCount(len(results))
        for row, result in enumerate(results):
            reasons = "; ".join(result.reasons[:3])
            if result.risk_flags:
                reasons = (reasons + " | " if reasons else "") + "Risk: " + "; ".join(result.risk_flags)
            signals = ", ".join(CHANNEL_LABELS.get(ch, ch) for ch in result.channels)
            values = [
                result.tier,
                result.ticker,
                str(result.score),
                str(result.event_count),
                str(result.channel_count),
                signals,
                reasons or "-",
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col in {2, 3, 4}:
                    item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row, col, item)


class LiveAlertsPage(QWidget):
    ticker_requested = Signal(str)

    def __init__(self, repo: DashboardRepository) -> None:
        super().__init__()
        self.repo = repo

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)

        title = QLabel("Live Alerts")
        title.setObjectName("pageTitle")
        subtitle = QLabel("Latest structured TrendVision events stored by the Windows listener.")
        subtitle.setObjectName("muted")
        root.addWidget(title)
        root.addWidget(subtitle)

        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter by ticker or text...")
        self.search.textChanged.connect(self.refresh)
        self.channel = QComboBox()
        self.channel.addItem("All channels", "")
        for key, label in CHANNEL_LABELS.items():
            self.channel.addItem(label, key)
        self.channel.currentIndexChanged.connect(self.refresh)
        filters.addWidget(self.search, 1)
        filters.addWidget(self.channel)
        root.addLayout(filters)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Time", "Ticker", "Channel", "Type", "Alert"])
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        for col in range(4):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.cellDoubleClicked.connect(self._open_ticker)
        root.addWidget(self.table, 1)

    def _open_ticker(self, row: int, _column: int) -> None:
        item = self.table.item(row, 1)
        if item and item.text() not in {"", "-"}:
            self.ticker_requested.emit(item.text())

    def refresh(self) -> None:
        channel = self.channel.currentData() or None
        events = self.repo.list_events(limit=300, channel=channel)
        query = self.search.text().strip().casefold()
        if query:
            events = [
                event for event in events
                if query in str(event.get("ticker") or "").casefold()
                or query in str(event.get("headline") or "").casefold()
                or query in str(event.get("channel") or "").casefold()
            ]

        self.table.setRowCount(len(events))
        for row, event in enumerate(events):
            values = [
                _display_time(event["received_at"]),
                event.get("ticker") or "-",
                CHANNEL_LABELS.get(event["channel"], event["channel"]),
                event["event_type"].replace("_", " "),
                event["headline"],
            ]
            for col, value in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(str(value)))


class TickerMemoryPage(QWidget):
    def __init__(self, repo: DashboardRepository) -> None:
        super().__init__()
        self.repo = repo
        self.current_ticker = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)

        top = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("Ticker Memory")
        title.setObjectName("pageTitle")
        subtitle = QLabel("Everything TrendVisionAI has accumulated for one ticker.")
        subtitle.setObjectName("muted")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        top.addLayout(title_box)
        top.addStretch()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Ticker, e.g. SCKT")
        self.search.setMaximumWidth(210)
        self.search.returnPressed.connect(self.load_from_search)
        go = QPushButton("Open")
        go.setObjectName("primary")
        go.clicked.connect(self.load_from_search)
        top.addWidget(self.search)
        top.addWidget(go)
        root.addLayout(top)

        self.empty = QLabel("Enter a ticker or double-click one in Attention List / Live Alerts.")
        self.empty.setObjectName("muted")
        root.addWidget(self.empty)

        metrics = QHBoxLayout()
        self.name_card = MetricCard("Ticker", "-")
        self.events_card = MetricCard("Total events", "0")
        self.channels_card = MetricCard("Channels", "0")
        self.last_card = MetricCard("Last seen", "-")
        for card in (self.name_card, self.events_card, self.channels_card, self.last_card):
            metrics.addWidget(card)
        root.addLayout(metrics)

        body = QHBoxLayout()
        left = QVBoxLayout()
        left_title = QLabel("Latest known facts")
        left_title.setStyleSheet("font-size: 12pt; font-weight: 600;")
        left.addWidget(left_title)
        self.facts = QTableWidget(0, 3)
        self.facts.setHorizontalHeaderLabels(["Field", "Value", "Source"])
        self.facts.verticalHeader().setVisible(False)
        self.facts.setEditTriggers(QTableWidget.NoEditTriggers)
        self.facts.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.facts.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.facts.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        left.addWidget(self.facts, 1)

        right = QVBoxLayout()
        right_title = QLabel("Activity timeline")
        right_title.setStyleSheet("font-size: 12pt; font-weight: 600;")
        right.addWidget(right_title)
        self.timeline = QTableWidget(0, 3)
        self.timeline.setHorizontalHeaderLabels(["Time", "Channel", "Alert"])
        self.timeline.verticalHeader().setVisible(False)
        self.timeline.setEditTriggers(QTableWidget.NoEditTriggers)
        self.timeline.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.timeline.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.timeline.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        right.addWidget(self.timeline, 1)

        body.addLayout(left, 1)
        body.addLayout(right, 2)
        root.addLayout(body, 1)

    def load_from_search(self) -> None:
        self.load_ticker(self.search.text())

    def load_ticker(self, ticker: str) -> None:
        ticker = ticker.upper().strip()
        if not ticker:
            return
        self.current_ticker = ticker
        self.search.setText(ticker)
        self.refresh()

    def refresh(self) -> None:
        if not self.current_ticker:
            return
        state = self.repo.ticker_state(self.current_ticker)
        if state is None:
            self.empty.setText(f"No stored scanner events for {self.current_ticker}.")
            self.facts.setRowCount(0)
            self.timeline.setRowCount(0)
            return

        self.empty.setText(
            f"Latest: {state['latest_event_type'].replace('_', ' ')} - {state['latest_headline']}"
        )
        self.name_card.set_value(state["ticker"])
        self.events_card.set_value(state["event_count"])
        self.channels_card.set_value(state["channel_count"])
        self.last_card.set_value(_display_time(state["last_seen_at"]))

        facts = state.get("facts") or {}
        preferred = [
            "signal", "price", "change_pct", "relative_volume", "float", "market_cap",
            "one_min_volume", "alert_number", "zero_borrow", "no_shares_available",
            "direction", "halt_status", "short_interest_pct", "ctb_fee_pct", "headline",
        ]
        keys = [key for key in preferred if key in facts]
        keys += [key for key in facts if key not in keys and key not in {"ticker", "raw_payload"}]
        self.facts.setRowCount(len(keys))
        for row, key in enumerate(keys):
            entry = facts.get(key) or {}
            value = entry.get("value") if isinstance(entry, dict) else entry
            source = entry.get("source_channel", "-") if isinstance(entry, dict) else "-"
            label = key.replace("_", " ").title()
            values = [label, str(value), CHANNEL_LABELS.get(source, source)]
            for col, text in enumerate(values):
                self.facts.setItem(row, col, QTableWidgetItem(text))

        events = self.repo.list_events(limit=200, ticker=self.current_ticker)
        self.timeline.setRowCount(len(events))
        for row, event in enumerate(events):
            values = [
                _display_time(event["received_at"]),
                CHANNEL_LABELS.get(event["channel"], event["channel"]),
                event["headline"],
            ]
            for col, text in enumerate(values):
                self.timeline.setItem(row, col, QTableWidgetItem(str(text)))


class SystemPage(QWidget):
    start_requested = Signal()
    stop_requested = Signal()

    def __init__(self, database_path: Path) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)

        title = QLabel("Listener & System")
        title.setObjectName("pageTitle")
        subtitle = QLabel("The desktop app starts the Windows notification listener directly.")
        subtitle.setObjectName("muted")
        root.addWidget(title)
        root.addWidget(subtitle)

        status_card = QFrame()
        status_card.setObjectName("card")
        status_layout = QHBoxLayout(status_card)
        self.status_dot = QLabel("●")
        self.status_dot.setStyleSheet("color: #8f98aa; font-size: 16pt;")
        self.status_text = QLabel("Stopped")
        self.status_text.setStyleSheet("font-size: 12pt; font-weight: 600;")
        status_layout.addWidget(self.status_dot)
        status_layout.addWidget(self.status_text)
        status_layout.addStretch()
        start = QPushButton("Start listener")
        start.setObjectName("primary")
        stop = QPushButton("Stop listener")
        stop.setObjectName("secondary")
        start.clicked.connect(self.start_requested)
        stop.clicked.connect(self.stop_requested)
        status_layout.addWidget(start)
        status_layout.addWidget(stop)
        root.addWidget(status_card)

        db = QLabel(f"Database: {database_path}")
        db.setObjectName("muted")
        root.addWidget(db)

        log_title = QLabel("Listener log")
        log_title.setStyleSheet("font-size: 12pt; font-weight: 600;")
        root.addWidget(log_title)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        root.addWidget(self.log, 1)

    def set_running(self, running: bool) -> None:
        if running:
            self.status_dot.setStyleSheet("color: #22c55e; font-size: 16pt;")
            self.status_text.setText("Listener running")
        else:
            self.status_dot.setStyleSheet("color: #8f98aa; font-size: 16pt;")
            self.status_text.setText("Listener stopped")

    def append_log(self, text: str) -> None:
        text = text.rstrip()
        if not text:
            return
        self.log.append(text.replace("\n", "<br>"))
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("TrendVisionAI")
        self.resize(1360, 820)
        self.setMinimumSize(1050, 650)

        config_path = Path("config.json")
        self.config = load_config(config_path if config_path.exists() else None)
        self.repo = DashboardRepository(self.config.database_path)
        self.listener_process = QProcess(self)
        self.listener_process.setProcessChannelMode(QProcess.MergedChannels)
        self.listener_process.readyReadStandardOutput.connect(self._read_listener_output)
        self.listener_process.started.connect(lambda: self.system_page.set_running(True))
        self.listener_process.finished.connect(lambda *_: self.system_page.set_running(False))
        self.listener_process.errorOccurred.connect(self._listener_error)

        shell = QWidget()
        self.setCentralWidget(shell)
        layout = QHBoxLayout(shell)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(215)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(16, 20, 16, 18)
        side.setSpacing(7)

        brand = QLabel("TrendVisionAI")
        brand.setObjectName("brand")
        accent = QLabel("LOCAL TRADING INTELLIGENCE")
        accent.setObjectName("brandAccent")
        side.addWidget(brand)
        side.addWidget(accent)
        side.addSpacing(18)

        self.stack = QStackedWidget()
        self.dashboard_page = DashboardPage(self.repo)
        self.alerts_page = LiveAlertsPage(self.repo)
        self.memory_page = TickerMemoryPage(self.repo)
        self.system_page = SystemPage(Path(self.config.database_path))
        self.stack.addWidget(self.dashboard_page)
        self.stack.addWidget(self.alerts_page)
        self.stack.addWidget(self.memory_page)
        self.stack.addWidget(self.system_page)

        nav_items = [
            ("Dashboard", 0),
            ("Live Alerts", 1),
            ("Ticker Memory", 2),
            ("Listener & System", 3),
        ]
        self.nav_buttons: list[QPushButton] = []
        for text, index in nav_items:
            button = QPushButton(text)
            button.setObjectName("nav")
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, i=index: self.navigate(i))
            side.addWidget(button)
            self.nav_buttons.append(button)
        side.addStretch()

        safety = QLabel("Analysis dashboard only\nNo automatic order execution")
        safety.setObjectName("muted")
        safety.setWordWrap(True)
        side.addWidget(safety)

        layout.addWidget(sidebar)
        layout.addWidget(self.stack, 1)

        self.dashboard_page.ticker_requested.connect(self.open_ticker)
        self.alerts_page.ticker_requested.connect(self.open_ticker)
        self.system_page.start_requested.connect(self.start_listener)
        self.system_page.stop_requested.connect(self.stop_listener)

        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(2000)
        self.refresh_timer.timeout.connect(self.refresh_visible_page)
        self.refresh_timer.start()

        self.navigate(0)
        self.refresh_visible_page()
        QTimer.singleShot(350, self.start_listener)

    def navigate(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        for i, button in enumerate(self.nav_buttons):
            button.setChecked(i == index)
        self.refresh_visible_page()

    def open_ticker(self, ticker: str) -> None:
        self.memory_page.load_ticker(ticker)
        self.navigate(2)

    def refresh_visible_page(self) -> None:
        widget = self.stack.currentWidget()
        refresh = getattr(widget, "refresh", None)
        if callable(refresh):
            try:
                refresh()
            except sqlite3.Error as exc:
                self.system_page.append_log(f"Database refresh error: {exc}")

    def start_listener(self) -> None:
        if self.listener_process.state() != QProcess.NotRunning:
            return
        env = QProcessEnvironment.systemEnvironment()
        project_root = Path.cwd()
        src_path = str(project_root / "src")
        current_pythonpath = env.value("PYTHONPATH")
        env.insert("PYTHONPATH", src_path + (os.pathsep + current_pythonpath if current_pythonpath else ""))
        env.insert("PYTHONUNBUFFERED", "1")
        self.listener_process.setProcessEnvironment(env)
        self.listener_process.setWorkingDirectory(str(project_root))
        self.listener_process.start(sys.executable, ["-m", "trendvision_ai.notification_api_listener"])
        self.system_page.append_log("Starting Windows notification listener...")

    def stop_listener(self) -> None:
        if self.listener_process.state() == QProcess.NotRunning:
            return
        self.system_page.append_log("Stopping listener...")
        self.listener_process.terminate()
        if not self.listener_process.waitForFinished(1800):
            self.listener_process.kill()

    def _read_listener_output(self) -> None:
        data = bytes(self.listener_process.readAllStandardOutput()).decode("utf-8", errors="replace")
        self.system_page.append_log(data)
        self.refresh_visible_page()

    def _listener_error(self, error: QProcess.ProcessError) -> None:
        self.system_page.append_log(f"Listener process error: {error}")
        self.system_page.set_running(False)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.stop_listener()
        event.accept()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("TrendVisionAI")
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLE)
    font = QFont("Segoe UI", 10)
    app.setFont(font)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
