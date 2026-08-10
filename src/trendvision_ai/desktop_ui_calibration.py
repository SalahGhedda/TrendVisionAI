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
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from . import desktop_ui_ai as ai
from .review_calibration import REVIEW_VERSION, analyze_snapshot_v3
from .review_outcomes import HORIZON_OPTIONS, OUTCOME_OPTIONS, ReviewOutcomeStore


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
        return "No Alpaca samples after this review yet."
    progress = "complete" if market.get("horizon_complete") else "in progress"
    target = int(market.get("target_minutes") or 0)
    coverage = float(market.get("coverage_minutes") or 0.0)
    return (
        f"Alpaca: {_price(market.get('reference_price'))} → {_price(market.get('last_price'))} "
        f"| Return {_pct(market.get('return_pct'))} | MFE {_pct(market.get('mfe_pct'))} "
        f"| MAE {_pct(market.get('mae_pct'))} | {int(market.get('sample_count') or 0)} samples "
        f"| coverage {coverage:.1f}/{target} min ({progress})."
    )


def _market_table_text(market: dict[str, Any]) -> tuple[str, str]:
    if not market.get("available"):
        return "-", "No Alpaca data"
    outcome = (
        f"R {_pct(market.get('return_pct'))} | "
        f"MFE {_pct(market.get('mfe_pct'))} | MAE {_pct(market.get('mae_pct'))}"
    )
    target = int(market.get("target_minutes") or 0)
    coverage = float(market.get("coverage_minutes") or 0.0)
    state = "done" if market.get("horizon_complete") else "live"
    coverage_text = (
        f"{coverage:.1f}/{target}m • {int(market.get('sample_count') or 0)} samples • {state}"
    )
    return outcome, coverage_text


class CalibrationTickerMemoryPage(ai.AITickerMemoryPage):
    def __init__(self, repo: ai.base.DashboardRepository) -> None:
        super().__init__(repo)
        self.outcome_store = ReviewOutcomeStore(repo.database_path)
        self.current_review_record: dict[str, Any] | None = None

        for label in self.findChildren(QLabel):
            if label.text() == "AI Candidate Review v2":
                label.setText(f"AI Candidate Review v{REVIEW_VERSION}")

        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 13, 16, 13)
        layout.setSpacing(8)

        title = QLabel("Review Outcome Journal")
        title.setStyleSheet("font-size: 12pt; font-weight: 600;")
        subtitle = QLabel(
            "After enough time has passed, label what actually happened. TrendVision follow-up and objective Alpaca measurements are saved together for calibration."
        )
        subtitle.setObjectName("muted")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        row = QHBoxLayout()
        self.outcome_combo = QComboBox()
        self.outcome_combo.addItems(OUTCOME_OPTIONS)
        self.horizon_combo = QComboBox()
        for label, minutes in HORIZON_OPTIONS.items():
            self.horizon_combo.addItem(label, minutes)
        self.horizon_combo.setCurrentText("30 min")
        self.horizon_combo.currentIndexChanged.connect(self._refresh_followup_only)
        save = QPushButton("Save outcome")
        save.setObjectName("primary")
        save.clicked.connect(self.save_outcome)
        row.addWidget(QLabel("Outcome"))
        row.addWidget(self.outcome_combo, 1)
        row.addWidget(QLabel("Review horizon"))
        row.addWidget(self.horizon_combo)
        row.addWidget(save)
        layout.addLayout(row)

        self.followup_status = QLabel(
            "Analyze a ticker first, then return later to label the outcome."
        )
        self.followup_status.setObjectName("muted")
        self.followup_status.setWordWrap(True)
        layout.addWidget(self.followup_status)

        self.market_outcome_status = QLabel("Objective market outcome: waiting for a review.")
        self.market_outcome_status.setObjectName("muted")
        self.market_outcome_status.setWordWrap(True)
        layout.addWidget(self.market_outcome_status)

        self.outcome_notes = QTextEdit()
        self.outcome_notes.setMaximumHeight(75)
        self.outcome_notes.setPlaceholderText(
            "Optional notes: e.g. continued after halt, collapsed immediately, never gave a clean entry..."
        )
        layout.addWidget(self.outcome_notes)

        root = self.layout()
        if root is not None:
            root.addWidget(card)

    def load_ticker(self, ticker: str) -> None:
        super().load_ticker(ticker)
        self._refresh_outcome(load_saved=True)

    def refresh(self) -> None:
        super().refresh()
        self._refresh_followup_only()

    def _analysis_completed(self, result: ai.AIReviewResult) -> None:
        super()._analysis_completed(result)
        self._refresh_outcome(load_saved=True)

    def _refresh_outcome(self, *, load_saved: bool) -> None:
        if not self.current_ticker:
            return
        record = self.outcome_store.latest_review_record(self.current_ticker)
        self.current_review_record = record
        if record is None:
            self.followup_status.setText(
                f"No AI review exists for {self.current_ticker} yet."
            )
            self.market_outcome_status.setText(
                "Objective market outcome: create an AI review first so measurements have a reference timestamp."
            )
            if load_saved:
                self.outcome_combo.setCurrentText("NOT LABELED")
                self.outcome_notes.clear()
            return

        saved = self.outcome_store.get_outcome(record["id"])
        if load_saved:
            if saved is None:
                self.outcome_combo.setCurrentText("NOT LABELED")
                self.outcome_notes.clear()
            else:
                self.outcome_combo.setCurrentText(saved["outcome"])
                target = int(saved["horizon_minutes"])
                for index in range(self.horizon_combo.count()):
                    if int(self.horizon_combo.itemData(index)) == target:
                        self.horizon_combo.blockSignals(True)
                        self.horizon_combo.setCurrentIndex(index)
                        self.horizon_combo.blockSignals(False)
                        break
                self.outcome_notes.setPlainText(saved["notes"])
        self._refresh_followup_only()

    def _refresh_followup_only(self) -> None:
        record = self.current_review_record
        if record is None:
            return
        horizon = int(self.horizon_combo.currentData() or 30)
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
        channels = ", ".join(followup["channels"]) or "none"
        result = record.get("result") or {}
        status = result.get("review_status") or result.get("verdict") or "-"
        self.followup_status.setText(
            f"Review #{record['id']} ({status}) at {record['created_at']} — "
            f"{followup['event_count']} later TrendVision event(s) across "
            f"{followup['channel_count']} channel(s) within {horizon} min [{channels}]."
        )
        self.market_outcome_status.setText(_market_summary_text(market))

    def save_outcome(self) -> None:
        record = self.current_review_record
        if record is None:
            self.followup_status.setText("Analyze/open a reviewed ticker first.")
            return
        outcome = self.outcome_combo.currentText().strip()
        if outcome == "NOT LABELED":
            self.followup_status.setText("Choose an outcome before saving.")
            return
        horizon = int(self.horizon_combo.currentData() or 30)
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
        self.outcome_store.save_outcome(
            review_id=record["id"],
            ticker=record["ticker"],
            horizon_minutes=horizon,
            outcome=outcome,
            notes=self.outcome_notes.toPlainText(),
            followup=followup,
            market_metrics=market,
        )
        suffix = ""
        if market.get("available") and not market.get("horizon_complete"):
            suffix = " The selected market horizon is still in progress, so the saved metrics are a partial snapshot."
        self.followup_status.setText(
            f"Outcome saved for review #{record['id']}: {outcome}. "
            f"TrendVision follow-up and Alpaca measurements are now part of the calibration sample.{suffix}"
        )


class CalibrationPage(QWidget):
    ticker_requested = Signal(str)

    def __init__(self, repo: ai.base.DashboardRepository) -> None:
        super().__init__()
        self.store = ReviewOutcomeStore(repo.database_path)

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)

        title = QLabel("Calibration Journal")
        title.setObjectName("pageTitle")
        subtitle = QLabel(
            "AI reviews, human outcome labels, TrendVision follow-up and objective Alpaca return/MFE/MAE measurements. Goal: collect enough real samples before automating review decisions."
        )
        subtitle.setObjectName("muted")
        subtitle.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(subtitle)

        metrics = QHBoxLayout()
        self.total_card = ai.base.MetricCard("AI reviews")
        self.labeled_card = ai.base.MetricCard("Outcomes labeled")
        self.unlabeled_card = ai.base.MetricCard("Waiting for outcome")
        self.market_card = ai.base.MetricCard("Reviews with market data")
        self.extreme_card = ai.base.MetricCard("Extreme-risk reviews")
        for card in (
            self.total_card,
            self.labeled_card,
            self.unlabeled_card,
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
                "Evidence",
                "Status",
                "Outcome",
                "Market outcome",
                "Market coverage",
                "Post-review alerts",
            ]
        )
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        for column in range(7):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(7, QHeaderView.Stretch)
        header.setSectionResizeMode(8, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(9, QHeaderView.Stretch)
        self.table.cellDoubleClicked.connect(self._open_ticker)
        root.addWidget(self.table, 1)

    def _open_ticker(self, row: int, _column: int) -> None:
        item = self.table.item(row, 1)
        if item and item.text():
            self.ticker_requested.emit(item.text())

    def refresh(self) -> None:
        reviews = self.store.list_reviews(limit=100)
        labeled = [item for item in reviews if item["outcome"] != "NOT LABELED"]
        self.total_card.set_value(len(reviews))
        self.labeled_card.set_value(len(labeled))
        self.unlabeled_card.set_value(len(reviews) - len(labeled))
        self.extreme_card.set_value(
            sum(
                1
                for item in reviews
                if str((item.get("review") or {}).get("risk_level") or "").upper()
                == "EXTREME"
            )
        )

        rows_for_ui: list[tuple[dict[str, Any], dict[str, Any], str]] = []
        market_covered = 0
        for item in reviews:
            if item["outcome"] == "NOT LABELED":
                horizon = 30
                followup = self.store.post_review_summary(
                    ticker=item["ticker"],
                    review_created_at=item["created_at"],
                    horizon_minutes=horizon,
                )
                followup_text = (
                    f"{followup['event_count']} events / {followup['channel_count']} channels (30m)"
                )
                market = self.store.market_summary(
                    ticker=item["ticker"],
                    review_created_at=item["created_at"],
                    horizon_minutes=horizon,
                )
            else:
                horizon = int(item.get("horizon_minutes") or 30)
                followup_text = (
                    f"{item['followup_event_count']} events / "
                    f"{item['followup_channel_count']} channels"
                )
                market = item.get("market_metrics") or {}
                if not market.get("available"):
                    market = self.store.market_summary(
                        ticker=item["ticker"],
                        review_created_at=item["created_at"],
                        horizon_minutes=horizon,
                    )
            if market.get("available"):
                market_covered += 1
            rows_for_ui.append((item, market, followup_text))

        self.market_card.set_value(market_covered)
        self.table.setRowCount(len(rows_for_ui))
        for row, (item, market, followup_text) in enumerate(rows_for_ui):
            review = item.get("review") or {}
            market_text, coverage_text = _market_table_text(market)
            values = [
                ai.base._display_time(item["created_at"]),
                item["ticker"],
                review.get("interest_level") or "LEGACY",
                review.get("risk_level") or "-",
                review.get("evidence_quality") or review.get("confidence") or "-",
                review.get("review_status") or review.get("verdict") or "-",
                item["outcome"],
                market_text,
                coverage_text,
                followup_text,
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                if column in {2, 3, 4}:
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
