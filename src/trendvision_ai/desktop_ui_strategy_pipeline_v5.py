from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
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

from . import desktop_ui_strategy_pipeline_v4 as current
from .api_call_log import OpenAIApiCallStore


base = current.base
API_CALL_DATABASE = Path("data") / "openai_api_calls.db"


def _display_time(value: Any) -> str:
    try:
        return datetime.fromisoformat(str(value)).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return str(value or "-")


def _duration(value: Any) -> str:
    try:
        milliseconds = int(value)
    except (TypeError, ValueError):
        return "-"
    if milliseconds < 1000:
        return f"{milliseconds} ms"
    return f"{milliseconds / 1000.0:.1f} s"


class ApiCallsPage(QWidget):
    """Small persistent audit page for automatic OpenAI requests."""

    def __init__(self, database_path: str | Path = API_CALL_DATABASE) -> None:
        super().__init__()
        self.store = OpenAIApiCallStore(database_path)
        self._render_revision: tuple[int, int, str] | None = None
        self._rows: list[dict[str, Any]] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)

        title = QLabel("API Calls")
        title.setObjectName("pageTitle")
        subtitle = QLabel(
            "Tracks automatic OpenAI Trade Plan requests so you can see exactly when TrendVisionAI became interested enough in a stock to ask the model for a chart/setup decision."
        )
        subtitle.setObjectName("muted")
        subtitle.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(subtitle)

        metrics = QHBoxLayout()
        self.total_card = base.MetricCard("Total calls")
        self.today_card = base.MetricCard("Today")
        self.completed_card = base.MetricCard("Completed")
        self.failed_card = base.MetricCard("Failed")
        self.active_card = base.MetricCard("In progress")
        for card in (
            self.total_card,
            self.today_card,
            self.completed_card,
            self.failed_card,
            self.active_card,
        ):
            metrics.addWidget(card)
        root.addLayout(metrics)

        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(
            [
                "Time",
                "Ticker",
                "Purpose",
                "Model",
                "Strategy",
                "Score",
                "Status",
                "Decision",
                "Duration",
            ]
        )
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        for column in (0, 1, 3, 5, 6, 7, 8):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._show_details)
        root.addWidget(self.table, 1)

        actions = QHBoxLayout()
        actions.addStretch()
        refresh_button = QPushButton("Refresh")
        refresh_button.setObjectName("secondary")
        refresh_button.clicked.connect(lambda: self.refresh(force=True))
        actions.addWidget(refresh_button)
        root.addLayout(actions)

        self.details = QLabel(
            "A row appears only when an actual automatic OpenAI Trade Plan request is attempted. Local strategy scans that find no setup do not count as API calls."
        )
        self.details.setObjectName("muted")
        self.details.setWordWrap(True)
        root.addWidget(self.details)

        note = QLabel(
            "This journal stores request metadata only (time, ticker, model, strategy, status and duration). It does not store your OpenAI API key or duplicate the full prompt/chart payload."
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        root.addWidget(note)

        self.refresh(force=True)

    def _show_details(self) -> None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._rows):
            return
        item = self._rows[row]
        strategy = item.get("strategy_name") or item.get("strategy_id") or "-"
        score = item.get("strategy_score")
        score_text = f"{score}/100" if score is not None else "-"
        message = (
            f"{item.get('ticker') or '-'} | {strategy} ({score_text}) | "
            f"{item.get('model') or '-'} / reasoning {item.get('reasoning_effort') or '-'} | "
            f"Status {item.get('status') or '-'} | Decision {item.get('decision') or '-'} | "
            f"Duration {_duration(item.get('duration_ms'))}."
        )
        error = str(item.get("error_text") or "").strip()
        if error:
            message += f" Error: {error}"
        self.details.setText(message)

    def refresh(self, *, force: bool = False) -> None:
        revision = self.store.revision()
        if not force and revision == self._render_revision:
            return

        stats = self.store.stats()
        self.total_card.set_value(stats.get("total") or 0)
        self.today_card.set_value(stats.get("today") or 0)
        self.completed_card.set_value(stats.get("completed") or 0)
        self.failed_card.set_value(stats.get("failed") or 0)
        self.active_card.set_value(stats.get("in_progress") or 0)

        rows = self.store.list_calls(limit=500)
        selected_id = None
        current_row = self.table.currentRow()
        if current_row >= 0:
            cell = self.table.item(current_row, 0)
            if cell is not None:
                selected_id = cell.data(Qt.UserRole)

        self._rows = rows
        self.table.setUpdatesEnabled(False)
        try:
            self.table.setRowCount(len(rows))
            restore_row = -1
            for row_index, item in enumerate(rows):
                values = [
                    _display_time(item.get("started_at")),
                    item.get("ticker") or "-",
                    item.get("purpose") or "-",
                    item.get("model") or "-",
                    item.get("strategy_name") or item.get("strategy_id") or "-",
                    item.get("strategy_score") if item.get("strategy_score") is not None else "-",
                    item.get("status") or "-",
                    item.get("decision") or "-",
                    _duration(item.get("duration_ms")),
                ]
                for column, value in enumerate(values):
                    cell = QTableWidgetItem(str(value))
                    if column != 4:
                        cell.setTextAlignment(Qt.AlignCenter)
                    if column == 0:
                        cell.setData(Qt.UserRole, int(item["id"]))
                    self.table.setItem(row_index, column, cell)
                if selected_id is not None and int(item["id"]) == int(selected_id):
                    restore_row = row_index

            if restore_row >= 0:
                self.table.selectRow(restore_row)
            elif rows:
                self.table.selectRow(0)
        finally:
            self.table.setUpdatesEnabled(True)

        self._render_revision = revision
        if rows:
            self._show_details()


PreviousMainWindow = base.MainWindow


class StrategyPipelineMainWindowV5(PreviousMainWindow):
    """Performance V4 plus a persistent automatic OpenAI-call audit page."""

    def __init__(self) -> None:
        super().__init__()

        self.api_calls_page = ApiCallsPage()
        page_index = self.stack.addWidget(self.api_calls_page)
        self.api_calls_button = QPushButton("API Calls")
        self.api_calls_button.setObjectName("nav")
        self.api_calls_button.setCheckable(True)
        self.api_calls_button.clicked.connect(
            lambda _checked=False, i=page_index: self.navigate(i)
        )

        sidebar_layout = self.nav_buttons[-1].parentWidget().layout()
        insert_at = sidebar_layout.indexOf(self.nav_buttons[-1])
        sidebar_layout.insertWidget(insert_at, self.api_calls_button)
        self.nav_buttons.append(self.api_calls_button)


base.MainWindow = StrategyPipelineMainWindowV5


def main() -> int:
    return current.main()


if __name__ == "__main__":
    raise SystemExit(main())
