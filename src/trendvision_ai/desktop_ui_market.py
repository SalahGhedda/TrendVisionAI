from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, QSettings, QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import desktop_ui_calibration as cal
from .automatic_outcomes import (
    SCOPE_MARKET_SESSION,
    AutomaticOutcomeStore,
)
from .market_data import (
    DEFAULT_FEED,
    AlpacaMarketClient,
    MarketDataStore,
    delete_alpaca_credentials,
    get_alpaca_credentials,
    parse_snapshot,
    save_alpaca_credentials,
)


class MarketPollWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        *,
        symbols: list[str],
        key_id: str,
        secret: str,
        feed: str,
    ) -> None:
        super().__init__()
        self.symbols = symbols
        self.key_id = key_id
        self.secret = secret
        self.feed = feed

    def run(self) -> None:
        try:
            client = AlpacaMarketClient(self.key_id, self.secret, feed=self.feed)
            self.completed.emit(client.fetch_snapshots(self.symbols))
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class MarketTrackerController(QObject):
    status_changed = Signal(str)
    data_updated = Signal()

    def __init__(self, repo: cal.ai.base.DashboardRepository, database_path) -> None:
        super().__init__()
        self.repo = repo
        self.store = MarketDataStore(database_path)
        self.outcomes = AutomaticOutcomeStore(database_path)
        self.settings = QSettings("TrendVisionAI", "TrendVisionAI")
        self.timer = QTimer(self)
        self.timer.setInterval(15_000)
        self.timer.timeout.connect(self.poll)
        self.worker: MarketPollWorker | None = None
        self._active_sessions: list[dict[str, Any]] = []
        self._feed = DEFAULT_FEED

    def start(self) -> None:
        self.timer.start()
        QTimer.singleShot(800, self.poll)

    def stop(self) -> None:
        self.timer.stop()
        if self.worker is not None and self.worker.isRunning():
            self.worker.requestInterruption()
            self.worker.wait(1500)

    def _refresh_automatic_outcomes(self) -> dict[str, int]:
        try:
            return self.outcomes.refresh_all_due_outcomes(limit=200)
        except Exception:
            return {"session_changes": 0, "review_changes": 0}

    def poll(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            return

        # Outcome classification is local and can run even if credentials are
        # temporarily unavailable. This also catches horizons that completed
        # since the previous poll.
        self._refresh_automatic_outcomes()

        self._feed = str(
            self.settings.value("alpaca/feed", DEFAULT_FEED) or DEFAULT_FEED
        ).strip().lower()
        credentials = get_alpaca_credentials()
        if credentials is None:
            self.status_changed.emit(
                "Alpaca market tracking is ready but not configured. Add API credentials under Listener & System."
            )
            self.data_updated.emit()
            return

        # Discovery remains TrendVision-only. The market API begins observing a
        # ticker only after our local attention engine marks it HIGH ATTENTION.
        try:
            attention = self.repo.attention_list(30, limit=100)
        except Exception as exc:
            self.status_changed.emit(f"Could not evaluate HIGH ATTENTION tickers: {exc}")
            return

        high = [item for item in attention if item.tier == "HIGH ATTENTION"]
        for item in high:
            self.store.ensure_session(
                ticker=item.ticker,
                trigger_tier=item.tier,
                trigger_score=item.score,
                feed=self._feed,
                tracking_minutes=240,
            )

        sessions = self.store.active_sessions(limit=30)
        self._active_sessions = sessions
        if not sessions:
            self.status_changed.emit(
                "Alpaca connected. Waiting for a ticker to reach HIGH ATTENTION. Automatic outcome calibration is active."
            )
            self.data_updated.emit()
            return

        symbols = list(dict.fromkeys(str(row["ticker"]).upper() for row in sessions))
        key_id, secret = credentials
        worker = MarketPollWorker(
            symbols=symbols,
            key_id=key_id,
            secret=secret,
            feed=self._feed,
        )
        self.worker = worker
        worker.completed.connect(self._poll_completed)
        worker.failed.connect(self._poll_failed)
        worker.finished.connect(self._poll_finished)
        worker.start()
        self.status_changed.emit(
            f"Tracking {len(symbols)} HIGH ATTENTION ticker(s) via Alpaca {self._feed.upper()} — refreshing every 15 seconds."
        )

    def _poll_completed(self, snapshots: object) -> None:
        payload = snapshots if isinstance(snapshots, dict) else {}
        session_by_ticker = {
            str(row["ticker"]).upper(): row for row in self._active_sessions
        }
        missing_ids: list[int] = []
        saved = 0
        for ticker, session in session_by_ticker.items():
            snapshot = payload.get(ticker)
            if not isinstance(snapshot, dict):
                missing_ids.append(int(session["id"]))
                continue
            sample = parse_snapshot(ticker, snapshot, feed=self._feed)
            self.store.save_sample(int(session["id"]), sample)
            saved += 1

        if missing_ids:
            self.store.set_error(
                missing_ids,
                "No snapshot returned for this symbol/feed on the latest poll.",
            )

        changes = self._refresh_automatic_outcomes()
        auto_changes = int(changes.get("session_changes") or 0) + int(
            changes.get("review_changes") or 0
        )
        suffix = (
            f" Automatic outcome engine updated {auto_changes} completed horizon(s)."
            if auto_changes
            else ""
        )
        self.status_changed.emit(
            f"Alpaca {self._feed.upper()}: saved {saved} market snapshot(s) for "
            f"{len(session_by_ticker)} active tracking session(s).{suffix}"
        )
        self.data_updated.emit()

    def _poll_failed(self, message: str) -> None:
        ids = [int(row["id"]) for row in self._active_sessions]
        self.store.set_error(ids, message)
        self._refresh_automatic_outcomes()
        self.status_changed.emit(f"Alpaca market-data error: {message}")
        self.data_updated.emit()

    def _poll_finished(self) -> None:
        if self.worker is not None:
            self.worker.deleteLater()
        self.worker = None


class MarketTrackingPage(QWidget):
    ticker_requested = Signal(str)

    def __init__(self, database_path) -> None:
        super().__init__()
        self.store = MarketDataStore(database_path)
        self.outcomes = AutomaticOutcomeStore(database_path)
        self._metrics_rows: list[dict[str, Any]] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)

        title = QLabel("Market Tracking")
        title.setObjectName("pageTitle")
        subtitle = QLabel(
            "HIGH ATTENTION tickers are automatically observed for up to 4 hours. TrendVision discovers them; Alpaca measures the price path; TrendVisionAI classifies completed horizons automatically."
        )
        subtitle.setObjectName("muted")
        subtitle.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(subtitle)

        self.status = QLabel("Waiting for market tracker status...")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        metrics = QHBoxLayout()
        self.active_card = cal.ai.base.MetricCard("Active sessions")
        self.total_card = cal.ai.base.MetricCard("Tracking sessions")
        self.samples_card = cal.ai.base.MetricCard("Samples stored")
        self.auto_card = cal.ai.base.MetricCard("Auto 15m outcomes")
        self.best_card = cal.ai.base.MetricCard("Best MFE")
        for card in (
            self.active_card,
            self.total_card,
            self.samples_card,
            self.auto_card,
            self.best_card,
        ):
            metrics.addWidget(card)
        root.addLayout(metrics)

        self.table = QTableWidget(0, 16)
        self.table.setHorizontalHeaderLabels(
            [
                "Started",
                "Ticker",
                "Trigger",
                "Auto 15m",
                "Reference",
                "Last",
                "Return",
                "MFE",
                "MAE",
                "Elapsed",
                "Max 1m Vol",
                "Bid",
                "Ask",
                "Spread",
                "Samples",
                "Feed / Status",
            ]
        )
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        for column in range(15):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(15, QHeaderView.Stretch)
        self.table.cellDoubleClicked.connect(self._open_ticker)
        self.table.itemSelectionChanged.connect(self._show_selected_details)
        root.addWidget(self.table, 1)

        detail_card = QFrame()
        detail_card.setObjectName("card")
        detail_layout = QVBoxLayout(detail_card)
        detail_layout.setContentsMargins(14, 10, 14, 10)
        detail_title = QLabel("Selected tracking session")
        detail_title.setStyleSheet("font-size: 11pt; font-weight: 600;")
        self.detail = QLabel(
            "Select a row to see automatic 15m / 30m / 60m / 4h outcomes and peak/trough measurements."
        )
        self.detail.setObjectName("muted")
        self.detail.setWordWrap(True)
        detail_layout.addWidget(detail_title)
        detail_layout.addWidget(self.detail)
        root.addWidget(detail_card)

        note = QLabel(
            "Automatic labels describe the observed price path; they are not buy/sell instructions. Reference price is the first successful Alpaca snapshot after HIGH ATTENTION. IEX reflects the IEX feed rather than consolidated SIP data."
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        root.addWidget(note)

    def set_status(self, text: str) -> None:
        self.status.setText(text)

    def _open_ticker(self, row: int, _column: int) -> None:
        item = self.table.item(row, 1)
        if item and item.text():
            self.ticker_requested.emit(item.text())

    @staticmethod
    def _price(value: Any) -> str:
        try:
            return f"${float(value):.4f}" if value is not None else "-"
        except (TypeError, ValueError):
            return "-"

    @staticmethod
    def _pct(value: Any) -> str:
        try:
            return f"{float(value):+.2f}%" if value is not None else "-"
        except (TypeError, ValueError):
            return "-"

    @staticmethod
    def _volume(value: Any) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return "-"
        if number >= 1_000_000:
            return f"{number / 1_000_000:.2f}M"
        if number >= 1_000:
            return f"{number / 1_000:.1f}K"
        return f"{number:.0f}"

    @staticmethod
    def _minutes(value: Any) -> str:
        try:
            return f"{float(value):.1f}m"
        except (TypeError, ValueError):
            return "-"

    @staticmethod
    def _auto_short(outcome: dict[str, Any] | None) -> str:
        if not outcome:
            return "WAITING"
        return f"{outcome.get('label') or '-'} [{outcome.get('confidence') or '-'}]"

    def _show_selected_details(self) -> None:
        row_index = self.table.currentRow()
        if row_index < 0 or row_index >= len(self._metrics_rows):
            return
        row = self._metrics_rows[row_index]
        session_id = int(row.get("id") or 0)
        peak_time = self._minutes(row.get("time_to_peak_minutes"))
        trough_time = self._minutes(row.get("time_to_trough_minutes"))

        outcome_lines: list[str] = []
        for horizon, label in ((15, "15m"), (30, "30m"), (60, "60m"), (240, "4h")):
            auto = self.outcomes.get_outcome(
                scope=SCOPE_MARKET_SESSION,
                subject_id=session_id,
                horizon_minutes=horizon,
            )
            if auto is None:
                outcome_lines.append(f"{label}: waiting")
                continue
            metrics = auto.get("metrics") or {}
            flags = ", ".join(auto.get("flags") or []) or "none"
            outcome_lines.append(
                f"{label}: {auto.get('label')} [{auto.get('confidence')}] | "
                f"R {self._pct(metrics.get('return_pct'))} | MFE {self._pct(metrics.get('mfe_pct'))} | "
                f"MAE {self._pct(metrics.get('mae_pct'))} | flags {flags}"
            )

        self.detail.setText(
            f"{row.get('ticker') or '-'} | Reference {self._price(row.get('reference_price'))} → "
            f"Last {self._price(row.get('last_price'))} ({self._pct(row.get('return_pct'))}) | "
            f"Peak {self._price(row.get('peak_price'))} at +{peak_time} | "
            f"Trough {self._price(row.get('trough_price'))} at +{trough_time} | "
            f"Max 1m volume {self._volume(row.get('max_minute_volume'))}\n"
            + "\n".join(outcome_lines)
        )

    def refresh(self) -> None:
        selected_ticker = None
        current = self.table.currentRow()
        if current >= 0:
            item = self.table.item(current, 1)
            selected_ticker = item.text() if item else None

        try:
            self.outcomes.refresh_due_session_outcomes(limit=200)
        except Exception:
            pass

        sessions = self.store.list_sessions(limit=100)
        metrics = [self.store.session_metrics(int(row["id"])) for row in sessions]
        self._metrics_rows = metrics
        active = [row for row in metrics if row.get("status") == "ACTIVE"]
        sample_total = sum(int(row.get("sample_count") or 0) for row in metrics)
        mfe_values = [
            float(row["mfe_pct"]) for row in metrics if row.get("mfe_pct") is not None
        ]
        auto_15_count = sum(
            1
            for row in metrics
            if self.outcomes.get_outcome(
                scope=SCOPE_MARKET_SESSION,
                subject_id=int(row.get("id") or 0),
                horizon_minutes=15,
            )
            is not None
        )

        self.active_card.set_value(len(active))
        self.total_card.set_value(len(metrics))
        self.samples_card.set_value(sample_total)
        self.auto_card.set_value(auto_15_count)
        self.best_card.set_value(f"{max(mfe_values):+.1f}%" if mfe_values else "-")

        self.table.setRowCount(len(metrics))
        restore_row = -1
        for row_index, row in enumerate(metrics):
            latest = row.get("last_sample") or {}
            auto_15 = self.outcomes.get_outcome(
                scope=SCOPE_MARKET_SESSION,
                subject_id=int(row.get("id") or 0),
                horizon_minutes=15,
            )
            feed_status = (
                f"{str(row.get('feed') or '-').upper()} / {row.get('status') or '-'}"
            )
            if row.get("last_error"):
                feed_status += f" — {row['last_error']}"
            values = [
                cal.ai.base._display_time(str(row.get("started_at") or "")),
                row.get("ticker") or "-",
                f"{row.get('trigger_tier') or '-'} ({row.get('trigger_score')})",
                self._auto_short(auto_15),
                self._price(row.get("reference_price")),
                self._price(row.get("last_price")),
                self._pct(row.get("return_pct")),
                self._pct(row.get("mfe_pct")),
                self._pct(row.get("mae_pct")),
                self._minutes(row.get("elapsed_minutes")),
                self._volume(row.get("max_minute_volume")),
                self._price(latest.get("bid")),
                self._price(latest.get("ask")),
                self._pct(latest.get("spread_pct")),
                str(row.get("sample_count") or 0),
                feed_status,
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                if column in {3, 6, 7, 8, 9, 10, 13, 14}:
                    cell.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row_index, column, cell)
            if selected_ticker and row.get("ticker") == selected_ticker and restore_row < 0:
                restore_row = row_index

        if restore_row >= 0:
            self.table.selectRow(restore_row)
        elif metrics and self.table.currentRow() < 0:
            self.table.selectRow(0)
        self._show_selected_details()


class MarketSystemPage(cal.ai.base.SystemPage):
    def __init__(self, database_path) -> None:
        super().__init__(database_path)
        self.market_settings = QSettings("TrendVisionAI", "TrendVisionAI")

        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 13, 16, 13)
        layout.setSpacing(8)

        title = QLabel("Alpaca Market Tracking")
        title.setStyleSheet("font-size: 12pt; font-weight: 600;")
        description = QLabel(
            "Used only to measure HIGH ATTENTION tickers after TrendVision discovers them. Completed 15m/30m/60m/4h outcomes are classified automatically. Credentials are stored in Windows Credential Manager, not config.json or GitHub."
        )
        description.setObjectName("muted")
        description.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(description)

        key_row = QHBoxLayout()
        self.alpaca_key = QLineEdit()
        self.alpaca_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.alpaca_key.setPlaceholderText("Alpaca API key ID")
        self.alpaca_secret = QLineEdit()
        self.alpaca_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.alpaca_secret.setPlaceholderText("Alpaca API secret key")
        save = QPushButton("Save Alpaca")
        save.setObjectName("primary")
        save.clicked.connect(self._save_alpaca)
        remove = QPushButton("Remove")
        remove.setObjectName("secondary")
        remove.clicked.connect(self._remove_alpaca)
        key_row.addWidget(self.alpaca_key, 1)
        key_row.addWidget(self.alpaca_secret, 1)
        key_row.addWidget(save)
        key_row.addWidget(remove)
        layout.addLayout(key_row)

        feed_row = QHBoxLayout()
        self.feed_combo = QComboBox()
        self.feed_combo.addItem("IEX — Basic/free real-time feed", "iex")
        self.feed_combo.addItem(
            "SIP — consolidated US exchanges (subscription required)", "sip"
        )
        self.feed_combo.addItem(
            "Delayed SIP — consolidated, 15 min delayed", "delayed_sip"
        )
        current_feed = str(
            self.market_settings.value("alpaca/feed", DEFAULT_FEED) or DEFAULT_FEED
        )
        for index in range(self.feed_combo.count()):
            if self.feed_combo.itemData(index) == current_feed:
                self.feed_combo.setCurrentIndex(index)
                break
        self.feed_combo.currentIndexChanged.connect(self._save_feed)
        feed_row.addWidget(QLabel("Market feed"))
        feed_row.addWidget(self.feed_combo, 1)
        layout.addLayout(feed_row)

        self.alpaca_status = QLabel()
        self.alpaca_status.setObjectName("muted")
        self.alpaca_status.setWordWrap(True)
        layout.addWidget(self.alpaca_status)
        self._refresh_alpaca_status()

        root = self.layout()
        if root is not None:
            root.insertWidget(4, card)

    def set_market_status(self, text: str) -> None:
        self.alpaca_status.setText(text)

    def _refresh_alpaca_status(self) -> None:
        feed = str(
            self.market_settings.value("alpaca/feed", DEFAULT_FEED) or DEFAULT_FEED
        ).upper()
        if get_alpaca_credentials():
            self.alpaca_status.setText(
                f"Alpaca credentials configured. Selected feed: {feed}. Automatic outcome calibration is enabled."
            )
        else:
            self.alpaca_status.setText(
                f"No Alpaca credentials configured yet. Selected feed: {feed}. TrendVision capture continues normally."
            )

    def _save_alpaca(self) -> None:
        try:
            save_alpaca_credentials(self.alpaca_key.text(), self.alpaca_secret.text())
        except Exception as exc:
            self.alpaca_status.setText(
                f"Could not save Alpaca credentials: {type(exc).__name__}: {exc}"
            )
            return
        self.alpaca_key.clear()
        self.alpaca_secret.clear()
        self._refresh_alpaca_status()

    def _remove_alpaca(self) -> None:
        delete_alpaca_credentials()
        self.alpaca_key.clear()
        self.alpaca_secret.clear()
        self._refresh_alpaca_status()

    def _save_feed(self) -> None:
        self.market_settings.setValue(
            "alpaca/feed", self.feed_combo.currentData() or DEFAULT_FEED
        )
        self._refresh_alpaca_status()


# Original MainWindow resolves SystemPage from desktop_ui's global namespace.
cal.ai.base.SystemPage = MarketSystemPage


class MarketMainWindow(cal.CalibrationMainWindow):
    def __init__(self) -> None:
        super().__init__()

        self.market_page = MarketTrackingPage(self.config.database_path)
        market_index = self.stack.addWidget(self.market_page)

        button = QPushButton("Market Tracking")
        button.setObjectName("nav")
        button.setCheckable(True)
        button.clicked.connect(lambda _checked=False, i=market_index: self.navigate(i))
        sidebar_layout = self.nav_buttons[-1].parentWidget().layout()
        insert_at = sidebar_layout.indexOf(self.nav_buttons[-1]) + 1
        sidebar_layout.insertWidget(insert_at, button)
        self.nav_buttons.append(button)
        self.market_page.ticker_requested.connect(self.open_ticker)

        self.market_controller = MarketTrackerController(
            self.repo, self.config.database_path
        )
        self.market_controller.status_changed.connect(self.market_page.set_status)
        self.market_controller.status_changed.connect(self.system_page.set_market_status)
        self.market_controller.data_updated.connect(self.market_page.refresh)
        self.market_controller.data_updated.connect(self.calibration_page.refresh)
        self.market_controller.start()

    def closeEvent(self, event) -> None:
        self.market_controller.stop()
        super().closeEvent(event)


cal.ai.base.MainWindow = MarketMainWindow


def main() -> int:
    return cal.ai.base.main()


if __name__ == "__main__":
    raise SystemExit(main())
