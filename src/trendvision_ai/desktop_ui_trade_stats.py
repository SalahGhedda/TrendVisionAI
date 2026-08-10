from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import Qt, Signal
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

from . import desktop_ui_fast_v3 as v3
from .qualification import (
    MIN_GLOBAL_RESOLVED,
    MIN_PATTERN_RESOLVED,
    CandidateQualificationEngine,
)
from .trade_plan_stats import TradePlanStatsEngine


base = v3.base
trade = v3.trade


def _rate(value: Any) -> str:
    try:
        return f"{float(value):.1f}%" if value is not None else "-"
    except (TypeError, ValueError):
        return "-"


def _pct(value: Any) -> str:
    try:
        return f"{float(value):+.2f}%" if value is not None else "-"
    except (TypeError, ValueError):
        return "-"


def _short_patterns(rows: list[dict[str, Any]], limit: int = 3) -> str:
    if not rows:
        return "none"
    names = [str(row.get("pattern") or "-") for row in rows[:limit]]
    suffix = f" +{len(rows) - limit} more" if len(rows) > limit else ""
    return "; ".join(names) + suffix


class TradePlanStatisticsPage(QWidget):
    def __init__(self, database_path) -> None:
        super().__init__()
        self.engine = TradePlanStatsEngine(database_path)
        self._rows: list[dict[str, Any]] = []
        self._last_engine_refresh = 0.0

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)

        top = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("Trade Plan Statistics")
        title.setObjectName("pageTitle")
        subtitle = QLabel(
            "Measures the experimental Entry / Stop / T1 / T2 plans against their objective follow-up and groups results by detection-time conditions and plan structure."
        )
        subtitle.setObjectName("muted")
        subtitle.setWordWrap(True)
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        top.addLayout(title_box, 1)

        self.minimum_combo = QComboBox()
        for label, samples in (
            ("Show all", 0),
            ("3+ resolved", 3),
            ("5+ resolved", 5),
            ("10+ resolved", 10),
            ("15+ resolved", 15),
            ("30+ resolved", 30),
        ):
            self.minimum_combo.addItem(label, samples)
        self.minimum_combo.currentIndexChanged.connect(self.refresh)
        refresh_button = QPushButton("Refresh")
        refresh_button.setObjectName("secondary")
        refresh_button.clicked.connect(self._force_refresh)
        top.addWidget(QLabel("Minimum evidence"))
        top.addWidget(self.minimum_combo)
        top.addWidget(refresh_button)
        root.addLayout(top)

        metrics = QHBoxLayout()
        self.potential_card = base.MetricCard("Potential plans")
        self.entered_card = base.MetricCard("Entry reached")
        self.t1_card = base.MetricCard("T1 reached")
        self.t2_card = base.MetricCard("T2 reached")
        self.stop_card = base.MetricCard("Stop first")
        for card in (
            self.potential_card,
            self.entered_card,
            self.t1_card,
            self.t2_card,
            self.stop_card,
        ):
            metrics.addWidget(card)
        root.addLayout(metrics)

        self.status = QLabel(
            "Trade-plan statistics fill automatically as actionable plans receive objective follow-up."
        )
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        self.table = QTableWidget(0, 11)
        self.table.setHorizontalHeaderLabels(
            [
                "Pattern",
                "Plans",
                "Entered",
                "Resolved",
                "Entry reached",
                "T1 reached",
                "T2 reached",
                "Stop first",
                "Median MFE",
                "Median MAE",
                "Evidence",
            ]
        )
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for column in range(1, 11):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._show_detail)
        root.addWidget(self.table, 1)

        detail_card = QFrame()
        detail_card.setObjectName("card")
        detail_layout = QVBoxLayout(detail_card)
        detail_layout.setContentsMargins(14, 10, 14, 10)
        detail_title = QLabel("Selected trade-plan pattern")
        detail_title.setStyleSheet("font-size: 11pt; font-weight: 600;")
        self.detail = QLabel("Select a row to see how its proposed plans behaved after entry.")
        self.detail.setObjectName("muted")
        self.detail.setWordWrap(True)
        detail_layout.addWidget(detail_title)
        detail_layout.addWidget(self.detail)
        root.addWidget(detail_card)

        warning = QLabel(
            "Rates use resolved post-entry cases. Open/unresolved plans are excluded from T1/T2/stop percentages. Sample maturity is <5 TOO EARLY, 5-14 EARLY, 15-29 BUILDING, 30+ MORE STABLE. These are calibration observations, not guaranteed win rates or expected profit."
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
        self.detail.setText(
            f"{row.get('pattern')} — {row.get('actionable_count')} actionable plan(s), "
            f"{row.get('entered_count')} entry observation(s), {row.get('resolved_count')} resolved post-entry case(s). "
            f"T1: {row.get('t1_reached_count')} | T2: {row.get('t2_reached_count')} | "
            f"Stop first: {row.get('stop_first_count')} | No target/no stop: {row.get('no_target_no_stop_count')} | "
            f"Unresolved after entry: {row.get('unresolved_entered_count')}. "
            f"Median planned R/R: T1 {row.get('median_planned_rr_t1') if row.get('median_planned_rr_t1') is not None else '-'}R, "
            f"T2 {row.get('median_planned_rr_t2') if row.get('median_planned_rr_t2') is not None else '-'}R."
        )

    def refresh(self) -> None:
        now = time.monotonic()
        if now - self._last_engine_refresh >= 15.0:
            try:
                changes = self.engine.refresh(limit=500)
                changed = int(changes.get("evaluation_changes") or 0)
                snapshots = int(changes.get("feature_changes") or 0)
                if changed or snapshots:
                    self.status.setText(
                        f"Updated {changed} trade-plan follow-up state(s) and created {snapshots} missing detection-time feature snapshot(s)."
                    )
            except Exception as exc:
                self.status.setText(f"Could not refresh trade-plan statistics: {type(exc).__name__}: {exc}")
                return
            self._last_engine_refresh = now

        minimum = int(self.minimum_combo.currentData() or 0)
        try:
            overview = self.engine.overview()
            rows = self.engine.pattern_stats(min_resolved=minimum)
        except Exception as exc:
            self.status.setText(f"Could not calculate trade-plan statistics: {type(exc).__name__}: {exc}")
            return

        self._rows = rows
        self.potential_card.set_value(overview.get("potential_plans") or 0)
        self.entered_card.set_value(overview.get("entered_count") or 0)
        self.t1_card.set_value(_rate(overview.get("t1_reached_pct")))
        self.t2_card.set_value(_rate(overview.get("t2_reached_pct")))
        self.stop_card.set_value(_rate(overview.get("stop_first_pct")))

        if not rows:
            self.status.setText(
                f"No trade-plan patterns have {minimum}+ resolved post-entry cases yet. Keep collecting plans; the page updates automatically."
            )

        selected = None
        current = self.table.currentRow()
        if current >= 0:
            item = self.table.item(current, 0)
            selected = item.text() if item else None

        self.table.setRowCount(len(rows))
        restore = -1
        for row_index, row in enumerate(rows):
            values = [
                row.get("pattern") or "-",
                str(row.get("actionable_count") or 0),
                str(row.get("entered_count") or 0),
                str(row.get("resolved_count") or 0),
                _rate(row.get("entry_reached_pct")),
                _rate(row.get("t1_reached_pct")),
                _rate(row.get("t2_reached_pct")),
                _rate(row.get("stop_first_pct")),
                _pct(row.get("median_max_return_pct")),
                _pct(row.get("median_max_drawdown_pct")),
                row.get("evidence") or "-",
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                if column >= 1:
                    cell.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row_index, column, cell)
            if selected and row.get("pattern") == selected:
                restore = row_index

        if restore >= 0:
            self.table.selectRow(restore)
        elif rows:
            self.table.selectRow(0)
        else:
            self.detail.setText("No resolved trade-plan pattern matches the current filter yet.")
        self._show_detail()


class CandidateQualificationPage(QWidget):
    ticker_requested = Signal(str)

    def __init__(self, repo) -> None:
        super().__init__()
        self.repo = repo
        self.engine = CandidateQualificationEngine(repo.database_path)
        self._rows: list[dict[str, Any]] = []
        self._last_engine_refresh = 0.0

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)

        title = QLabel("Candidate Qualification")
        title.setObjectName("pageTitle")
        subtitle = QLabel(
            "Experimental evidence gate for HIGH ATTENTION tickers. It matches the current detection-time conditions against resolved Trade Plan history before deciding whether a candidate deserves the chart/trade-plan stage. No trade alert is sent from this page yet."
        )
        subtitle.setObjectName("muted")
        subtitle.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(subtitle)

        metrics = QHBoxLayout()
        self.high_card = base.MetricCard("HIGH ATTENTION")
        self.qualified_card = base.MetricCard("Experimentally qualified")
        self.monitor_card = base.MetricCard("Monitor / risk")
        self.insufficient_card = base.MetricCard("Insufficient evidence")
        self.resolved_card = base.MetricCard("Resolved plan cases")
        for card in (
            self.high_card,
            self.qualified_card,
            self.monitor_card,
            self.insufficient_card,
            self.resolved_card,
        ):
            metrics.addWidget(card)
        root.addLayout(metrics)

        self.status = QLabel(
            f"Qualification stays locked until at least {MIN_GLOBAL_RESOLVED} resolved entered trade-plan cases exist globally; each contributing pattern needs {MIN_PATTERN_RESOLVED}+ resolved cases."
        )
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            [
                "Ticker",
                "Qualification",
                "Matched patterns",
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
        for column in range(6):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.Stretch)
        self.table.cellDoubleClicked.connect(self._open_ticker)
        self.table.itemSelectionChanged.connect(self._show_detail)
        root.addWidget(self.table, 1)

        detail_card = QFrame()
        detail_card.setObjectName("card")
        detail_layout = QVBoxLayout(detail_card)
        detail_layout.setContentsMargins(14, 10, 14, 10)
        detail_title = QLabel("Selected qualification evidence")
        detail_title.setStyleSheet("font-size: 11pt; font-weight: 600;")
        self.detail = QLabel("Select a candidate to see its strongest matched evidence.")
        self.detail.setObjectName("muted")
        self.detail.setWordWrap(True)
        detail_layout.addWidget(detail_title)
        detail_layout.addWidget(self.detail)
        root.addWidget(detail_card)

        note = QLabel(
            "EXPERIMENTALLY QUALIFIED is not an entry signal. It only means the detection-time conditions passed the historical evidence gate and may proceed to the chart + Trade Plan stage. Entry, stop and targets still require a separate fresh Trade Plan analysis."
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
            f"Positive mature patterns: {_short_patterns(row.get('positive_patterns') or [])}. "
            f"Negative mature patterns: {_short_patterns(row.get('negative_patterns') or [])}. "
            f"Neutral mature patterns: {_short_patterns(row.get('neutral_patterns') or [])}. "
            f"Immature matched patterns: {len(row.get('immature_patterns') or [])}."
        )

    def refresh(self) -> None:
        now = time.monotonic()
        if now - self._last_engine_refresh >= 15.0:
            try:
                self.engine.refresh(limit=500)
            except Exception as exc:
                self.status.setText(f"Could not refresh qualification evidence: {type(exc).__name__}: {exc}")
                return
            self._last_engine_refresh = now

        try:
            attention = self.repo.attention_list(30, limit=100)
        except Exception as exc:
            self.status.setText(f"Could not evaluate HIGH ATTENTION candidates: {type(exc).__name__}: {exc}")
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
        self.qualified_card.set_value(sum(1 for row in rows if row.get("status") == "EXPERIMENTALLY QUALIFIED"))
        self.monitor_card.set_value(sum(1 for row in rows if str(row.get("status") or "").startswith("MONITOR")))
        self.insufficient_card.set_value(sum(1 for row in rows if row.get("status") == "INSUFFICIENT EVIDENCE"))
        self.resolved_card.set_value(global_resolved)

        if global_resolved < MIN_GLOBAL_RESOLVED:
            self.status.setText(
                f"Qualification evidence is still locked: {global_resolved}/{MIN_GLOBAL_RESOLVED} resolved entered trade-plan cases collected globally. The engine is already matching patterns in the background."
            )
        elif rows:
            self.status.setText(
                f"Qualification v1 active with {global_resolved} resolved plan cases. Candidates still require a separate chart + Trade Plan analysis before any actionable levels exist."
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
                str(row.get("matched_pattern_count") or 0),
                str(len(row.get("positive_patterns") or [])),
                str(len(row.get("negative_patterns") or [])),
                str(row.get("global_resolved") or 0),
                row.get("reason") or "-",
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                if column in {1, 2, 3, 4, 5}:
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


PreviousMainWindow = base.MainWindow


class TradeStatsMainWindow(PreviousMainWindow):
    def __init__(self) -> None:
        super().__init__()

        self.trade_stats_page = TradePlanStatisticsPage(self.config.database_path)
        stats_index = self.stack.addWidget(self.trade_stats_page)
        stats_button = QPushButton("Trade Plan Statistics")
        stats_button.setObjectName("nav")
        stats_button.setCheckable(True)
        stats_button.clicked.connect(lambda _checked=False, i=stats_index: self.navigate(i))

        self.qualification_page = CandidateQualificationPage(self.repo)
        qualification_index = self.stack.addWidget(self.qualification_page)
        qualification_button = QPushButton("Candidate Qualification")
        qualification_button.setObjectName("nav")
        qualification_button.setCheckable(True)
        qualification_button.clicked.connect(
            lambda _checked=False, i=qualification_index: self.navigate(i)
        )
        self.qualification_page.ticker_requested.connect(self.open_ticker)

        sidebar_layout = self.nav_buttons[-1].parentWidget().layout()
        insert_at = sidebar_layout.indexOf(self.nav_buttons[-1]) + 1
        sidebar_layout.insertWidget(insert_at, stats_button)
        sidebar_layout.insertWidget(insert_at + 1, qualification_button)
        self.nav_buttons.extend([stats_button, qualification_button])


base.MainWindow = TradeStatsMainWindow


def main() -> int:
    return v3.main()


if __name__ == "__main__":
    raise SystemExit(main())
