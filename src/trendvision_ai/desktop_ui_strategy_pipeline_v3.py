from __future__ import annotations

from datetime import datetime
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import desktop_ui_strategy_pipeline_v2 as current
from .trade_alert_journal import TradeAlertJournalStore


base = current.base


def _display_time(value: Any) -> str:
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return str(value or "-")


def _price(value: Any) -> str:
    try:
        return f"${float(value):.4f}" if value is not None else "-"
    except (TypeError, ValueError):
        return "-"


def _rr(value: Any) -> str:
    try:
        return f"{float(value):.2f}R" if value is not None else "-"
    except (TypeError, ValueError):
        return "-"


class TradeAlertsPage(QWidget):
    result_changed = Signal()

    def __init__(self, store: TradeAlertJournalStore, live_store: Any) -> None:
        super().__init__()
        self.store = store
        self.live_store = live_store

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)

        title = QLabel("Trades")
        title.setObjectName("pageTitle")
        subtitle = QLabel(
            "Every FINAL_TRADE_ALERT is saved here instead of using a popup notification. "
            "The trade levels remain available after the alert, and you can manually mark the trade WIN or LOSS afterward."
        )
        subtitle.setObjectName("muted")
        subtitle.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(subtitle)

        metrics = QHBoxLayout()
        self.total_card = base.MetricCard("Total alerts")
        self.open_card = base.MetricCard("Open / unmarked")
        self.win_card = base.MetricCard("Wins")
        self.loss_card = base.MetricCard("Losses")
        self.rate_card = base.MetricCard("Manual win rate")
        for card in (
            self.total_card,
            self.open_card,
            self.win_card,
            self.loss_card,
            self.rate_card,
        ):
            metrics.addWidget(card)
        root.addLayout(metrics)

        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels(
            [
                "Time",
                "Ticker",
                "Strategy",
                "Entry",
                "SL",
                "TP1",
                "TP2",
                "R/R T1",
                "R/R T2",
                "Result",
            ]
        )
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        for column in (0, 1, 3, 4, 5, 6, 7, 8, 9):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        root.addWidget(self.table, 1)

        actions = QHBoxLayout()
        self.win_button = QPushButton("Mark WIN")
        self.win_button.setObjectName("primary")
        self.win_button.clicked.connect(lambda: self._set_result("WIN"))
        self.loss_button = QPushButton("Mark LOSS")
        self.loss_button.setObjectName("secondary")
        self.loss_button.clicked.connect(lambda: self._set_result("LOSS"))
        self.reset_button = QPushButton("Reset to OPEN")
        self.reset_button.setObjectName("secondary")
        self.reset_button.clicked.connect(lambda: self._set_result("OPEN"))
        refresh_button = QPushButton("Refresh")
        refresh_button.setObjectName("secondary")
        refresh_button.clicked.connect(self.refresh)
        actions.addWidget(self.win_button)
        actions.addWidget(self.loss_button)
        actions.addWidget(self.reset_button)
        actions.addStretch()
        actions.addWidget(refresh_button)
        root.addLayout(actions)

        self.details = QLabel("Select a trade to see its exact alert levels and IDs.")
        self.details.setObjectName("muted")
        self.details.setWordWrap(True)
        root.addWidget(self.details)

        note = QLabel(
            "WIN/LOSS is your manual journal label. It is intentionally separate from TrendVisionAI's objective post-plan evaluation/calibration data, so manually marking a result does not rewrite the measured market outcome."
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        root.addWidget(note)

        self.refresh()

    def _selected_alert_id(self) -> int | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        if item is None:
            return None
        value = item.data(Qt.UserRole)
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _selection_changed(self) -> None:
        alert_id = self._selected_alert_id()
        if alert_id is None:
            self.details.setText("Select a trade to see its exact alert levels and IDs.")
            return
        alert = self.store.get_alert(alert_id)
        if not alert:
            return
        entry = f"{_price(alert.get('entry_low'))} – {_price(alert.get('entry_high'))}"
        self.details.setText(
            f"{alert.get('ticker') or '-'} | {alert.get('strategy_name') or alert.get('strategy_id') or '-'} | "
            f"Entry {entry} | SL {_price(alert.get('stop_loss'))} | "
            f"TP1 {_price(alert.get('target_1'))} | TP2 {_price(alert.get('target_2'))} | "
            f"Plan #{alert.get('plan_id') or '-'} | Session #{alert.get('session_id') or '-'} | "
            f"Manual result: {alert.get('manual_result') or 'OPEN'}"
        )

    def _set_result(self, result: str) -> None:
        alert_id = self._selected_alert_id()
        if alert_id is None:
            self.details.setText("Select a trade row first, then choose WIN, LOSS, or Reset to OPEN.")
            return
        self.store.set_manual_result(alert_id, result)
        self.refresh(select_alert_id=alert_id)
        self.result_changed.emit()

    def refresh(self, select_alert_id: int | None = None) -> None:
        self.store.sync_from_live_events(self.live_store, limit=1000)
        stats = self.store.stats()
        self.total_card.set_value(stats["total"])
        self.open_card.set_value(stats["open"])
        self.win_card.set_value(stats["wins"])
        self.loss_card.set_value(stats["losses"])
        rate = stats.get("manual_win_rate_pct")
        self.rate_card.set_value(f"{rate:.1f}%" if rate is not None else "-")

        alerts = self.store.list_alerts(limit=500)
        self.table.setRowCount(len(alerts))
        selected_row = -1
        for row_index, alert in enumerate(alerts):
            entry = f"{_price(alert.get('entry_low'))}–{_price(alert.get('entry_high'))}"
            values = [
                _display_time(alert.get("created_at")),
                alert.get("ticker") or "-",
                alert.get("strategy_name") or alert.get("strategy_id") or "-",
                entry,
                _price(alert.get("stop_loss")),
                _price(alert.get("target_1")),
                _price(alert.get("target_2")),
                _rr(alert.get("risk_reward_target_1")),
                _rr(alert.get("risk_reward_target_2")),
                alert.get("manual_result") or "OPEN",
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                if column != 2:
                    cell.setTextAlignment(Qt.AlignCenter)
                if column == 0:
                    cell.setData(Qt.UserRole, int(alert["id"]))
                self.table.setItem(row_index, column, cell)
            if select_alert_id is not None and int(alert["id"]) == int(select_alert_id):
                selected_row = row_index

        if selected_row >= 0:
            self.table.selectRow(selected_row)
        elif alerts and self.table.currentRow() < 0:
            self.table.selectRow(0)
        else:
            self._selection_changed()


class StrategyPipelineMainWindowV3(current.StrategyPipelineMainWindowV2):
    """Strategy pipeline with a persistent in-app trade journal and no trade popups."""

    def __init__(self) -> None:
        super().__init__()
        self.trade_alert_journal = TradeAlertJournalStore(self.config.database_path)
        self.trade_alert_journal.sync_from_live_events(self.live_store, limit=1000)

        self.trades_page = TradeAlertsPage(self.trade_alert_journal, self.live_store)
        trades_index = self.stack.addWidget(self.trades_page)
        self.trades_button = QPushButton("Trades")
        self.trades_button.setObjectName("nav")
        self.trades_button.setCheckable(True)
        self.trades_button.clicked.connect(lambda _checked=False, i=trades_index: self.navigate(i))

        sidebar_layout = self.nav_buttons[-1].parentWidget().layout()
        insert_at = sidebar_layout.indexOf(self.nav_buttons[-1]) + 1
        sidebar_layout.insertWidget(insert_at, self.trades_button)
        self.nav_buttons.append(self.trades_button)
        self.trades_page.result_changed.connect(self._refresh_trade_badge)
        self._refresh_trade_badge()

    def _notify(self, title: str, message: str, *, urgent: bool = False) -> None:
        # Final entry/SL/TP alerts now live in the persistent Trades page instead
        # of appearing as ephemeral Windows popup notifications.
        if "TRADE ALERT" in str(title or "").upper():
            return
        super()._notify(title, message, urgent=urgent)

    def _refresh_trade_badge(self) -> None:
        stats = self.trade_alert_journal.stats()
        open_count = int(stats.get("open") or 0)
        self.trades_button.setText(f"Trades ({open_count})" if open_count else "Trades")

    def _auto_plan_completed(self, *args: Any, **kwargs: Any) -> None:
        super()._auto_plan_completed(*args, **kwargs)
        self.trade_alert_journal.sync_from_live_events(self.live_store, limit=1000)
        self.trades_page.refresh()
        self._refresh_trade_badge()


base.MainWindow = StrategyPipelineMainWindowV3


def main() -> int:
    return current.main()


if __name__ == "__main__":
    raise SystemExit(main())
