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

from . import desktop_ui_strategy_pipeline_v5 as current
from .missed_opportunities import DEFAULT_MAJOR_MOVE_PCT, MissedOpportunityAnalyzer


base = current.base


def _display_time(value: Any) -> str:
    try:
        return datetime.fromisoformat(str(value)).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return str(value or "-")


def _pct(value: Any) -> str:
    try:
        return f"{float(value):+.1f}%" if value is not None else "-"
    except (TypeError, ValueError):
        return "-"


def _minutes(value: Any) -> str:
    try:
        return f"{float(value):.1f}m" if value is not None else "-"
    except (TypeError, ValueError):
        return "-"


class MissedOpportunitiesPage(QWidget):
    """Hindsight diagnostics for HIGH ATTENTION sessions that later ran."""

    def __init__(self, database_path: str | Path) -> None:
        super().__init__()
        self.analyzer = MissedOpportunityAnalyzer(database_path)
        self._rows: list[dict[str, Any]] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)

        title = QLabel("Missed Opportunities")
        title.setObjectName("pageTitle")
        subtitle = QLabel(
            f"Diagnostics for recent HIGH ATTENTION tracking sessions. A 'major runner' currently means at least +{DEFAULT_MAJOR_MOVE_PCT:.0f}% MFE after tracking began. "
            "The page explains where the pipeline stopped: no setup, Terra WATCH/REJECT, hard gate, or final alert. Hindsight movement does not mean an entry was safely available."
        )
        subtitle.setObjectName("muted")
        subtitle.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(subtitle)

        metrics = QHBoxLayout()
        self.major_card = base.MetricCard("Major runners")
        self.alerted_card = base.MetricCard("Alerted")
        self.missed_card = base.MetricCard("Missed")
        self.no_setup_card = base.MetricCard("No setup")
        self.terra_card = base.MetricCard("Terra filtered")
        self.gate_card = base.MetricCard("Hard gate")
        for card in (
            self.major_card,
            self.alerted_card,
            self.missed_card,
            self.no_setup_card,
            self.terra_card,
            self.gate_card,
        ):
            metrics.addWidget(card)
        root.addLayout(metrics)

        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels(
            [
                "Started",
                "Ticker",
                "Attention",
                "MFE",
                "Time to peak",
                "Setup matches",
                "Terra plans",
                "Decisions",
                "Final alert",
                "Diagnosis",
            ]
        )
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        for column in range(9):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(9, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._show_details)
        root.addWidget(self.table, 1)

        actions = QHBoxLayout()
        actions.addStretch()
        refresh_button = QPushButton("Refresh diagnostics")
        refresh_button.setObjectName("secondary")
        refresh_button.clicked.connect(self.refresh)
        actions.addWidget(refresh_button)
        root.addLayout(actions)

        self.details = QLabel(
            "Select a row to see why TrendVisionAI did or did not produce a final trade alert."
        )
        self.details.setObjectName("muted")
        self.details.setWordWrap(True)
        root.addWidget(self.details)

        note = QLabel(
            "MFE is measured from the first successful Alpaca sample after HIGH ATTENTION. It is a diagnostic of what happened afterward, not a backtested claim that the full move could have been captured. IEX remains partial-venue data."
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        root.addWidget(note)

    def refresh(self) -> None:
        try:
            rows = self.analyzer.recent_rows(limit=40)
        except Exception as exc:
            self.details.setText(f"Could not build missed-opportunity diagnostics: {type(exc).__name__}: {exc}")
            return
        self._rows = rows
        stats = self.analyzer.stats(rows)
        self.major_card.set_value(stats["major_runners"])
        self.alerted_card.set_value(stats["alerted"])
        self.missed_card.set_value(stats["missed"])
        self.no_setup_card.set_value(stats["no_setup"])
        self.terra_card.set_value(stats["terra_filtered"])
        self.gate_card.set_value(stats["hard_gate"])

        self.table.setUpdatesEnabled(False)
        try:
            self.table.setRowCount(len(rows))
            for row_index, row in enumerate(rows):
                decisions = list(dict.fromkeys(row.get("decisions") or []))
                values = [
                    _display_time(row.get("started_at")),
                    row.get("ticker") or "-",
                    row.get("trigger_score") if row.get("trigger_score") is not None else "-",
                    _pct(row.get("mfe_pct")),
                    _minutes(row.get("time_to_peak_minutes")),
                    row.get("strategy_matches") or 0,
                    row.get("terra_plans") or 0,
                    ", ".join(decisions) if decisions else "-",
                    "YES" if row.get("final_alerts") else "NO",
                    row.get("diagnosis") or "-",
                ]
                for column, value in enumerate(values):
                    cell = QTableWidgetItem(str(value))
                    if column != 9:
                        cell.setTextAlignment(Qt.AlignCenter)
                    self.table.setItem(row_index, column, cell)
        finally:
            self.table.setUpdatesEnabled(True)

        if rows:
            self.table.selectRow(0)
            self._show_details()
        else:
            self.details.setText("No market tracking sessions are available yet.")

    def _show_details(self) -> None:
        row_index = self.table.currentRow()
        if row_index < 0 or row_index >= len(self._rows):
            return
        row = self._rows[row_index]
        blockers = row.get("blockers") or []
        blocker_text = f" | Gate blockers: {', '.join(blockers)}" if blockers else ""
        self.details.setText(
            f"{row.get('ticker') or '-'} | Session #{row.get('session_id') or '-'} | "
            f"MFE {_pct(row.get('mfe_pct'))}, MAE {_pct(row.get('mae_pct'))}, peak after {_minutes(row.get('time_to_peak_minutes'))}. "
            f"{row.get('reason') or ''}{blocker_text}"
        )


PreviousMainWindow = base.MainWindow


class StrategyPipelineMainWindowV6(PreviousMainWindow):
    """V5 plus momentum missed-opportunity diagnostics."""

    def __init__(self) -> None:
        super().__init__()

        self.missed_page = MissedOpportunitiesPage(self.config.database_path)
        page_index = self.stack.addWidget(self.missed_page)
        self.missed_button = QPushButton("Missed Opportunities")
        self.missed_button.setObjectName("nav")
        self.missed_button.setCheckable(True)
        self.missed_button.clicked.connect(
            lambda _checked=False, i=page_index: self._open_missed_page(i)
        )

        sidebar_layout = self.nav_buttons[-1].parentWidget().layout()
        insert_at = sidebar_layout.indexOf(self.nav_buttons[-1])
        sidebar_layout.insertWidget(insert_at, self.missed_button)
        self.nav_buttons.append(self.missed_button)

    def _open_missed_page(self, page_index: int) -> None:
        self.missed_page.refresh()
        self.navigate(page_index)


base.MainWindow = StrategyPipelineMainWindowV6


def main() -> int:
    return current.main()


if __name__ == "__main__":
    raise SystemExit(main())
