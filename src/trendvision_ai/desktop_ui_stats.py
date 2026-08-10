from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import desktop_ui_market as market
from .calibration_stats import CalibrationStatsEngine


HORIZONS = {
    "15 min": 15,
    "30 min": 30,
    "60 min": 60,
    "4 hours": 240,
}


def _pct(value: Any) -> str:
    try:
        return f"{float(value):+.2f}%" if value is not None else "-"
    except (TypeError, ValueError):
        return "-"


def _rate(value: Any) -> str:
    try:
        return f"{float(value):.1f}%" if value is not None else "-"
    except (TypeError, ValueError):
        return "-"


class CalibrationStatisticsPage(QWidget):
    def __init__(self, database_path) -> None:
        super().__init__()
        self.engine = CalibrationStatsEngine(database_path)
        self._rows: list[dict[str, Any]] = []
        self._last_engine_refresh = 0.0

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)

        top = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("Calibration Statistics")
        title.setObjectName("pageTitle")
        subtitle = QLabel(
            "Detection-time TrendVision conditions compared with automatic post-alert market outcomes. This is observational calibration data, not a trading signal."
        )
        subtitle.setObjectName("muted")
        subtitle.setWordWrap(True)
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        top.addLayout(title_box, 1)

        self.horizon_combo = QComboBox()
        for label, minutes in HORIZONS.items():
            self.horizon_combo.addItem(label, minutes)
        self.horizon_combo.setCurrentText("15 min")
        self.horizon_combo.currentIndexChanged.connect(self.refresh)

        self.minimum_combo = QComboBox()
        for label, samples in (
            ("Show all", 1),
            ("3+ samples", 3),
            ("5+ samples", 5),
            ("10+ samples", 10),
            ("20+ samples", 20),
        ):
            self.minimum_combo.addItem(label, samples)
        self.minimum_combo.currentIndexChanged.connect(self.refresh)

        refresh_button = QPushButton("Refresh")
        refresh_button.setObjectName("secondary")
        refresh_button.clicked.connect(self._force_refresh)

        top.addWidget(QLabel("Outcome horizon"))
        top.addWidget(self.horizon_combo)
        top.addWidget(QLabel("Minimum evidence"))
        top.addWidget(self.minimum_combo)
        top.addWidget(refresh_button)
        root.addLayout(top)

        metrics = QHBoxLayout()
        self.usable_card = market.cal.ai.base.MetricCard("Usable outcomes")
        self.pattern_card = market.cal.ai.base.MetricCard("Patterns shown")
        self.up_card = market.cal.ai.base.MetricCard("Up continuation")
        self.reversal_card = market.cal.ai.base.MetricCard("Spike / reversal")
        self.insufficient_card = market.cal.ai.base.MetricCard("Insufficient data")
        for card in (
            self.usable_card,
            self.pattern_card,
            self.up_card,
            self.reversal_card,
            self.insufficient_card,
        ):
            metrics.addWidget(card)
        root.addLayout(metrics)

        self.status = QLabel(
            "Statistics become useful gradually as automatic outcomes accumulate."
        )
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(
            [
                "Detection-time pattern",
                "Samples",
                "Median Return",
                "Median MFE",
                "Median MAE",
                "Up continuation",
                "Spike / reversal",
                "Negative",
                "Evidence",
            ]
        )
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for column in range(1, 9):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._show_detail)
        root.addWidget(self.table, 1)

        detail_card = QFrame()
        detail_card.setObjectName("card")
        detail_layout = QVBoxLayout(detail_card)
        detail_layout.setContentsMargins(14, 10, 14, 10)
        detail_title = QLabel("Selected pattern")
        detail_title.setStyleSheet("font-size: 11pt; font-weight: 600;")
        self.detail = QLabel(
            "Select a row to see the automatic outcome distribution for that detection-time condition."
        )
        self.detail.setObjectName("muted")
        self.detail.setWordWrap(True)
        detail_layout.addWidget(detail_title)
        detail_layout.addWidget(self.detail)
        root.addWidget(detail_card)

        warning = QLabel(
            "Do not tune qualification rules from tiny samples. TrendVisionAI marks <5 observations as TOO EARLY, 5-14 as EARLY, 15-29 as BUILDING, and 30+ as MORE STABLE. These labels describe sample maturity, not statistical proof or expected profit."
        )
        warning.setObjectName("muted")
        warning.setWordWrap(True)
        root.addWidget(warning)

    def _force_refresh(self) -> None:
        self._last_engine_refresh = 0.0
        self.refresh()

    def _show_detail(self) -> None:
        row_index = self.table.currentRow()
        if row_index < 0 or row_index >= len(self._rows):
            return
        row = self._rows[row_index]
        counts = row.get("label_counts") or {}
        distribution = " | ".join(
            f"{label}: {count}"
            for label, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        ) or "No outcomes"
        self.detail.setText(
            f"{row.get('pattern')} — {row.get('sample_count')} sample(s), evidence: "
            f"{row.get('evidence')}. Outcome distribution: {distribution}. "
            f"Mixed/two-sided: {_rate(row.get('volatile_mixed_pct'))}."
        )

    def refresh(self) -> None:
        horizon = int(self.horizon_combo.currentData() or 15)
        minimum = int(self.minimum_combo.currentData() or 1)

        now = time.monotonic()
        if now - self._last_engine_refresh >= 15.0:
            try:
                changes = self.engine.refresh(limit=500)
            except Exception as exc:
                self.status.setText(
                    f"Could not refresh calibration engine: {type(exc).__name__}: {exc}"
                )
                return
            self._last_engine_refresh = now
            changed = int(changes.get("outcome_changes") or 0)
            snapshots = int(changes.get("feature_changes") or 0)
            if changed or snapshots:
                self.status.setText(
                    f"Calibration engine updated {changed} completed outcome horizon(s) and created {snapshots} detection-time feature snapshot(s)."
                )

        try:
            overview = self.engine.overview(horizon)
            rows = self.engine.pattern_stats(
                horizon_minutes=horizon,
                min_samples=minimum,
            )
        except Exception as exc:
            self.status.setText(
                f"Could not calculate calibration statistics: {type(exc).__name__}: {exc}"
            )
            return

        self._rows = rows
        self.usable_card.set_value(overview.get("usable") or 0)
        self.pattern_card.set_value(len(rows))
        self.up_card.set_value(_rate(overview.get("up_rate_pct")))
        self.reversal_card.set_value(_rate(overview.get("reversal_rate_pct")))
        self.insufficient_card.set_value(overview.get("insufficient") or 0)

        if not rows:
            self.status.setText(
                f"No patterns meet the current {horizon}m / {minimum}+ sample filter yet. Keep TrendVisionAI running; this page fills automatically as completed outcomes accumulate."
            )

        selected_pattern = None
        current = self.table.currentRow()
        if current >= 0:
            item = self.table.item(current, 0)
            selected_pattern = item.text() if item else None

        self.table.setRowCount(len(rows))
        restore_row = -1
        for row_index, row in enumerate(rows):
            values = [
                row.get("pattern") or "-",
                str(row.get("sample_count") or 0),
                _pct(row.get("median_return_pct")),
                _pct(row.get("median_mfe_pct")),
                _pct(row.get("median_mae_pct")),
                _rate(row.get("up_continuation_pct")),
                _rate(row.get("spike_reversal_pct")),
                _rate(row.get("negative_pct")),
                row.get("evidence") or "-",
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                if column >= 1:
                    cell.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row_index, column, cell)
            if selected_pattern and row.get("pattern") == selected_pattern:
                restore_row = row_index

        if restore_row >= 0:
            self.table.selectRow(restore_row)
        elif rows:
            self.table.selectRow(0)
        else:
            self.detail.setText(
                "No completed objective outcomes match the current filter yet."
            )
        self._show_detail()


class StatsMainWindow(market.MarketMainWindow):
    def __init__(self) -> None:
        super().__init__()

        self.stats_page = CalibrationStatisticsPage(self.config.database_path)
        stats_index = self.stack.addWidget(self.stats_page)

        button = QPushButton("Calibration Statistics")
        button.setObjectName("nav")
        button.setCheckable(True)
        button.clicked.connect(lambda _checked=False, i=stats_index: self.navigate(i))
        sidebar_layout = self.nav_buttons[-1].parentWidget().layout()
        insert_at = sidebar_layout.indexOf(self.nav_buttons[-1]) + 1
        sidebar_layout.insertWidget(insert_at, button)
        self.nav_buttons.append(button)


market.cal.ai.base.MainWindow = StatsMainWindow


def main() -> int:
    return market.cal.ai.base.main()


if __name__ == "__main__":
    raise SystemExit(main())
