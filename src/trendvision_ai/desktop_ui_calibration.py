from __future__ import annotations

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

from . import desktop_ui_ai as ai
from .automatic_outcomes import SCOPE_AI_REVIEW, AutomaticOutcomeStore
from .review_calibration import REVIEW_VERSION, analyze_snapshot_v3
from .review_outcomes import HORIZON_OPTIONS, ReviewOutcomeStore


# ReviewWorker in desktop_ui_ai resolves this module-global function when its
# thread runs, so swapping it here upgrades analysis without duplicating the UI.
ai.analyze_snapshot = analyze_snapshot_v3


def _price(value: Any) -> str:
    try:
        return f"${float(value):.4f}" if value is not None else "-"
    except (TypeError, ValueError):
        return "-"


def _pct(value: Any) -> str:
    try:
        return f"{float(value):+.2f}%" if value is not None else "-"
    except (TypeError, ValueError):
        return "-"


def _market_summary_text(market: dict[str, Any]) -> str:
    if not market.get("available"):
        if market.get("horizon_complete"):
            return "Alpaca: horizon completed but there was not enough fresh market data to measure it."
        return "Alpaca: waiting for market samples after this review."
    progress = "complete" if market.get("horizon_complete") else "in progress"
    target = int(market.get("target_minutes") or 0)
    coverage = float(market.get("coverage_minutes") or 0.0)
    return (
        f"Alpaca: {_price(market.get('reference_price'))} → {_price(market.get('last_price'))} "
        f"| Return {_pct(market.get('return_pct'))} | MFE {_pct(market.get('mfe_pct'))} "
        f"| MAE {_pct(market.get('mae_pct'))} | {int(market.get('sample_count') or 0)} samples "
        f"| coverage {coverage:.1f}/{target} min ({progress})."
    )


def _market_table_text(market: dict[str, Any]) -> str:
    if not market.get("available"):
        return "-"
    return (
        f"R {_pct(market.get('return_pct'))} | "
        f"MFE {_pct(market.get('mfe_pct'))} | MAE {_pct(market.get('mae_pct'))}"
    )


def _auto_short(outcome: dict[str, Any] | None) -> str:
    if not outcome:
        return "WAITING"
    return f"{outcome.get('label') or '-'} [{outcome.get('confidence') or '-'}]"


def _auto_detail(outcome: dict[str, Any] | None, horizon: int) -> str:
    if not outcome:
        return f"Automatic {horizon}m outcome: waiting for the horizon to complete."
    flags = ", ".join(outcome.get("flags") or []) or "none"
    return (
        f"Automatic {horizon}m outcome: {outcome.get('label')} "
        f"[{outcome.get('confidence')} confidence] — {outcome.get('reason')} "
        f"Risk/quality flags: {flags}."
    )


class CalibrationTickerMemoryPage(ai.AITickerMemoryPage):
    def __init__(self, repo: ai.base.DashboardRepository) -> None:
        super().__init__(repo)
        self.outcome_store = ReviewOutcomeStore(repo.database_path)
        self.auto_store = AutomaticOutcomeStore(repo.database_path)
        self.current_review_record: dict[str, Any] | None = None

        for label in self.findChildren(QLabel):
            if label.text() == "AI Candidate Review v2":
                label.setText(f"AI Candidate Review v{REVIEW_VERSION}")

        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 13, 16, 13)
        layout.setSpacing(8)

        title = QLabel("Automatic Outcome Calibration")
        title.setStyleSheet("font-size: 12pt; font-weight: 600;")
        subtitle = QLabel(
            "No trading judgment is required from you. After each horizon completes, TrendVisionAI automatically classifies the observed price path from Alpaca data and keeps the raw measurements for calibration."
        )
        subtitle.setObjectName("muted")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        row = QHBoxLayout()
        self.horizon_combo = QComboBox()
        for label, minutes in HORIZON_OPTIONS.items():
            self.horizon_combo.addItem(label, minutes)
        self.horizon_combo.setCurrentText("15 min")
        self.horizon_combo.currentIndexChanged.connect(self._refresh_followup_only)
        row.addWidget(QLabel("View horizon"))
        row.addWidget(self.horizon_combo)
        row.addStretch(1)
        layout.addLayout(row)

        self.auto_status = QLabel("Automatic outcome: waiting for an AI review.")
        self.auto_status.setWordWrap(True)
        layout.addWidget(self.auto_status)

        self.followup_status = QLabel(
            "TrendVision follow-up: waiting for an AI review."
        )
        self.followup_status.setObjectName("muted")
        self.followup_status.setWordWrap(True)
        layout.addWidget(self.followup_status)

        self.market_outcome_status = QLabel("Alpaca measurements: waiting for an AI review.")
        self.market_outcome_status.setObjectName("muted")
        self.market_outcome_status.setWordWrap(True)
        layout.addWidget(self.market_outcome_status)

        note = QLabel(
            "Automatic labels describe the observed path (for example strong continuation, spike then reversal, or mixed/range). They are calibration data, not buy/sell instructions."
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        layout.addWidget(note)

        root = self.layout()
        if root is not None:
            root.addWidget(card)

    def load_ticker(self, ticker: str) -> None:
        super().load_ticker(ticker)
        self._refresh_review()

    def refresh(self) -> None:
        super().refresh()
        self._refresh_followup_only()

    def _analysis_completed(self, result: ai.AIReviewResult) -> None:
        super()._analysis_completed(result)
        self._refresh_review()

    def _refresh_review(self) -> None:
        if not self.current_ticker:
            return
        record = self.outcome_store.latest_review_record(self.current_ticker)
        self.current_review_record = record
        if record is None:
            self.auto_status.setText(
                f"Automatic outcome: no AI review exists for {self.current_ticker} yet."
            )
            self.followup_status.setText("TrendVision follow-up: no AI review timestamp yet.")
            self.market_outcome_status.setText("Alpaca measurements: no AI review timestamp yet.")
            return
        self._refresh_followup_only()

    def _refresh_followup_only(self) -> None:
        record = self.current_review_record
        if record is None:
            return

        try:
            self.auto_store.refresh_due_review_outcomes(limit=200)
        except Exception:
            pass

        horizon = int(self.horizon_combo.currentData() or 15)
        followup = self.outcome_store.post_review_summary(
            ticker=record["ticker"],
            review_created_at=record["created_at"],
            horizon_minutes=horizon,
        )
        market = self.outcome_store.market_summary(
            ticker=record["ticker"],
            review_created_at=record["created_at"],
            horizon_minutes=horizon,
        )
        auto = self.auto_store.get_outcome(
            scope=SCOPE_AI_REVIEW,
            subject_id=record["id"],
            horizon_minutes=horizon,
        )

        channels = ", ".join(followup["channels"]) or "none"
        result = record.get("result") or {}
        status = result.get("review_status") or result.get("verdict") or "-"
        self.auto_status.setText(_auto_detail(auto, horizon))
        self.followup_status.setText(
            f"Review #{record['id']} ({status}) at {record['created_at']} — "
            f"{followup['event_count']} later TrendVision event(s) across "
            f"{followup['channel_count']} channel(s) within {horizon} min [{channels}]."
        )
        self.market_outcome_status.setText(_market_summary_text(market))


class CalibrationPage(QWidget):
    ticker_requested = Signal(str)

    def __init__(self, repo: ai.base.DashboardRepository) -> None:
        super().__init__()
        self.store = ReviewOutcomeStore(repo.database_path)
        self.auto_store = AutomaticOutcomeStore(repo.database_path)

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)

        title = QLabel("Calibration Journal")
        title.setObjectName("pageTitle")
        subtitle = QLabel(
            "AI reviews are compared with automatically measured 15m / 30m / 60m / 4h market outcomes. No manual trading labels are required."
        )
        subtitle.setObjectName("muted")
        subtitle.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(subtitle)

        metrics = QHBoxLayout()
        self.total_card = ai.base.MetricCard("AI reviews")
        self.auto_card = ai.base.MetricCard("Auto 15m outcomes")
        self.waiting_card = ai.base.MetricCard("Waiting for 15m")
        self.market_card = ai.base.MetricCard("Reviews with 15m data")
        self.extreme_card = ai.base.MetricCard("Extreme-risk reviews")
        for card in (
            self.total_card,
            self.auto_card,
            self.waiting_card,
            self.market_card,
            self.extreme_card,
        ):
            metrics.addWidget(card)
        root.addLayout(metrics)

        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels(
            [
                "Reviewed",
                "Ticker",
                "Interest",
                "Risk",
                "AI Status",
                "Auto 15m",
                "15m Market",
                "Auto 30m",
                "Auto 60m",
                "Auto 4h",
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
        for column in (7, 8, 9):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.table.cellDoubleClicked.connect(self._open_ticker)
        root.addWidget(self.table, 1)

    def _open_ticker(self, row: int, _column: int) -> None:
        item = self.table.item(row, 1)
        if item and item.text():
            self.ticker_requested.emit(item.text())

    def refresh(self) -> None:
        try:
            self.auto_store.refresh_due_review_outcomes(limit=200)
        except Exception:
            pass

        reviews = self.store.list_reviews(limit=100)
        self.total_card.set_value(len(reviews))
        self.extreme_card.set_value(
            sum(
                1
                for item in reviews
                if str((item.get("review") or {}).get("risk_level") or "").upper()
                == "EXTREME"
            )
        )

        auto_15_count = 0
        market_15_count = 0
        rows_for_ui: list[tuple[dict[str, Any], dict[str, Any], dict[int, dict[str, Any] | None]]] = []
        for item in reviews:
            auto_by_horizon = {
                horizon: self.auto_store.get_outcome(
                    scope=SCOPE_AI_REVIEW,
                    subject_id=item["id"],
                    horizon_minutes=horizon,
                )
                for horizon in (15, 30, 60, 240)
            }
            if auto_by_horizon[15] is not None:
                auto_15_count += 1
            market_15 = self.store.market_summary(
                ticker=item["ticker"],
                review_created_at=item["created_at"],
                horizon_minutes=15,
            )
            if market_15.get("available"):
                market_15_count += 1
            rows_for_ui.append((item, market_15, auto_by_horizon))

        self.auto_card.set_value(auto_15_count)
        self.waiting_card.set_value(max(0, len(reviews) - auto_15_count))
        self.market_card.set_value(market_15_count)

        self.table.setRowCount(len(rows_for_ui))
        for row, (item, market_15, auto_by_horizon) in enumerate(rows_for_ui):
            review = item.get("review") or {}
            values = [
                ai.base._display_time(item["created_at"]),
                item["ticker"],
                review.get("interest_level") or "LEGACY",
                review.get("risk_level") or "-",
                review.get("review_status") or review.get("verdict") or "-",
                _auto_short(auto_by_horizon[15]),
                _market_table_text(market_15),
                _auto_short(auto_by_horizon[30]),
                _auto_short(auto_by_horizon[60]),
                _auto_short(auto_by_horizon[240]),
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                if column in {2, 3, 5, 7, 8, 9}:
                    cell.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row, column, cell)


class CalibrationMainWindow(ai.base.MainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.calibration_page = CalibrationPage(self.repo)
        page_index = self.stack.addWidget(self.calibration_page)

        button = QPushButton("Calibration Journal")
        button.setObjectName("nav")
        button.setCheckable(True)
        button.clicked.connect(lambda _checked=False, i=page_index: self.navigate(i))

        sidebar_layout = self.nav_buttons[-1].parentWidget().layout()
        insert_at = sidebar_layout.indexOf(self.nav_buttons[-1]) + 1
        sidebar_layout.insertWidget(insert_at, button)
        self.nav_buttons.append(button)
        self.calibration_page.ticker_requested.connect(self.open_ticker)


# Patch the base shell before base.main() constructs the window/pages.
ai.base.TickerMemoryPage = CalibrationTickerMemoryPage
ai.base.MainWindow = CalibrationMainWindow


def main() -> int:
    return ai.base.main()


if __name__ == "__main__":
    raise SystemExit(main())
