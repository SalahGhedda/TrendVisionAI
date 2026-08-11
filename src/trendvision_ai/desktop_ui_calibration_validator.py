from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .qualification import CandidateQualificationEngine


def _short_patterns(rows: list[dict[str, Any]], limit: int = 3) -> str:
    if not rows:
        return "none"
    names = [str(row.get("pattern") or "-") for row in rows[:limit]]
    suffix = f" +{len(rows) - limit} more" if len(rows) > limit else ""
    return "; ".join(names) + suffix


class CalibrationValidatorPage(QWidget):
    ticker_requested = Signal(str)

    def __init__(self, repo, metric_card_class) -> None:
        super().__init__()
        self.repo = repo
        self.engine = CandidateQualificationEngine(repo.database_path)
        self._rows: list[dict[str, Any]] = []
        self._last_engine_refresh = 0.0

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)

        title = QLabel("Calibration Validator")
        title.setObjectName("pageTitle")
        subtitle = QLabel(
            "Historical TrendVisionAI evidence is now a validator/filter, not the source of the trading strategy. Known setups come from the Strategy Library. Mature negative evidence can veto a setup; immature history alone does not block it."
        )
        subtitle.setObjectName("muted")
        subtitle.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(subtitle)

        metrics = QHBoxLayout()
        self.high_card = metric_card_class("HIGH ATTENTION")
        self.supported_card = metric_card_class("Historically supported")
        self.risk_card = metric_card_class("Historical risk")
        self.immature_card = metric_card_class("Immature / neutral")
        self.resolved_card = metric_card_class("Resolved plan cases")
        for card in (
            self.high_card,
            self.supported_card,
            self.risk_card,
            self.immature_card,
            self.resolved_card,
        ):
            metrics.addWidget(card)
        root.addLayout(metrics)

        self.status = QLabel(
            "The validator becomes more informative as strategy-specific and detection-condition plan outcomes accumulate."
        )
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            [
                "Ticker",
                "Calibration",
                "Positive mature",
                "Negative mature",
                "Global resolved",
                "Reason",
            ]
        )
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        for column in range(5):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.Stretch)
        self.table.cellDoubleClicked.connect(self._open_ticker)
        self.table.itemSelectionChanged.connect(self._show_detail)
        root.addWidget(self.table, 1)

        detail_card = QFrame()
        detail_card.setObjectName("card")
        detail_layout = QVBoxLayout(detail_card)
        detail_layout.setContentsMargins(14, 10, 14, 10)
        detail_title = QLabel("Selected calibration evidence")
        detail_title.setStyleSheet("font-size: 11pt; font-weight: 600;")
        self.detail = QLabel("Select a candidate to inspect its matched historical conditions.")
        self.detail.setObjectName("muted")
        self.detail.setWordWrap(True)
        detail_layout.addWidget(detail_title)
        detail_layout.addWidget(self.detail)
        root.addWidget(detail_card)

        note = QLabel(
            "INSUFFICIENT EVIDENCE and MONITOR are not automatic vetoes anymore. MONITOR / RISK means mature negative matched conditions exist and the live hard gate can block an alert. Strategy-specific validation is also checked separately at alert time."
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        root.addWidget(note)

    def _open_ticker(self, row: int, _column: int) -> None:
        item = self.table.item(row, 0)
        if item and item.text():
            self.ticker_requested.emit(item.text())

    def _show_detail(self) -> None:
        row_index = self.table.currentRow()
        if row_index < 0 or row_index >= len(self._rows):
            return
        row = self._rows[row_index]
        self.detail.setText(
            f"{row.get('ticker')} — {row.get('status')}. "
            f"Positive mature conditions: {_short_patterns(row.get('positive_patterns') or [])}. "
            f"Negative mature conditions: {_short_patterns(row.get('negative_patterns') or [])}. "
            f"Neutral mature conditions: {_short_patterns(row.get('neutral_patterns') or [])}. "
            f"Immature matched conditions: {len(row.get('immature_patterns') or [])}."
        )

    def refresh(self) -> None:
        now = time.monotonic()
        if now - self._last_engine_refresh >= 30.0:
            try:
                self.engine.refresh(limit=500)
            except Exception as exc:
                self.status.setText(f"Calibration refresh deferred: {type(exc).__name__}: {exc}")
            self._last_engine_refresh = now

        try:
            attention = self.repo.attention_list(30, limit=100)
        except Exception as exc:
            self.status.setText(f"Could not read HIGH ATTENTION candidates: {type(exc).__name__}: {exc}")
            return

        high = [item for item in attention if item.tier == "HIGH ATTENTION"]
        rows: list[dict[str, Any]] = []
        for item in high:
            result = self.engine.qualify_ticker(item.ticker)
            result["attention_score"] = item.score
            rows.append(result)
        self._rows = rows

        global_resolved = int(self.engine.trade_stats.overview().get("resolved_count") or 0)
        self.high_card.set_value(len(rows))
        self.supported_card.set_value(sum(1 for row in rows if row.get("status") == "EXPERIMENTALLY SUPPORTED"))
        self.risk_card.set_value(sum(1 for row in rows if row.get("status") == "MONITOR / RISK"))
        self.immature_card.set_value(
            sum(1 for row in rows if row.get("status") in {"INSUFFICIENT EVIDENCE", "MONITOR"})
        )
        self.resolved_card.set_value(global_resolved)

        if rows:
            self.status.setText(
                f"{global_resolved} resolved entered Trade Plan case(s) available. The Strategy Library can operate before this history is mature; historical negative evidence is used as a veto/filter."
            )
        else:
            self.status.setText("No HIGH ATTENTION ticker is active in the current 30-minute window.")

        selected_ticker = None
        current = self.table.currentRow()
        if current >= 0:
            item = self.table.item(current, 0)
            selected_ticker = item.text() if item else None

        self.table.setRowCount(len(rows))
        restore = -1
        for row_index, row in enumerate(rows):
            values = [
                row.get("ticker") or "-",
                row.get("status") or "-",
                str(len(row.get("positive_patterns") or [])),
                str(len(row.get("negative_patterns") or [])),
                str(row.get("global_resolved") or 0),
                row.get("reason") or "-",
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                if column in {1, 2, 3, 4}:
                    cell.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row_index, column, cell)
            if selected_ticker and row.get("ticker") == selected_ticker:
                restore = row_index

        if restore >= 0:
            self.table.selectRow(restore)
        elif rows:
            self.table.selectRow(0)
        else:
            self.detail.setText("No HIGH ATTENTION candidate is active right now.")
        self._show_detail()
