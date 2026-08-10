from __future__ import annotations

import shutil
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSettings, QThread, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
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

from . import desktop_ui_stats as stats
from .ai_review import DEFAULT_MODEL, build_review_snapshot, get_api_key
from .attention import evaluate_attention
from .trade_plans import (
    TradePlanResult,
    TradePlanStore,
    analyze_trade_plan,
    build_trade_plan_snapshot,
)


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


def _rr(value: Any) -> str:
    try:
        return f"{float(value):.2f}R" if value is not None else "-"
    except (TypeError, ValueError):
        return "-"


def _format_plan(result: dict[str, Any], evaluation: dict[str, Any] | None = None) -> str:
    decision = str(result.get("decision") or "-")
    lines = [
        f"DECISION: {decision}    CONFIDENCE: {result.get('confidence') or '-'}    RISK: {result.get('risk_level') or '-'}",
        f"CHART: {result.get('chart_structure') or '-'}    SETUP: {result.get('setup_type') or '-'}",
        "",
        str(result.get("summary") or ""),
    ]

    if decision == "POTENTIAL TRADE":
        lines.extend([
            "",
            "EXPERIMENTAL LONG PLAN",
            f"Entry zone: {_price(result.get('entry_low'))} – {_price(result.get('entry_high'))}",
            f"Stop loss: {_price(result.get('stop_loss'))}",
            f"Target 1: {_price(result.get('target_1'))}    R/R: {_rr(result.get('risk_reward_target_1'))}",
            f"Target 2: {_price(result.get('target_2'))}    R/R: {_rr(result.get('risk_reward_target_2'))}",
            f"Entry trigger: {result.get('entry_trigger') or '-'}",
            f"Invalidation: {result.get('invalidation') or '-'}",
        ])
    else:
        lines.extend([
            "",
            "No actionable levels were saved. v1 only keeps entry/stop/targets when the case passes POTENTIAL TRADE guardrails.",
        ])

    for title, values in (
        ("Chart observations", result.get("chart_observations") or []),
        ("Positive factors", result.get("positive_factors") or []),
        ("Risk factors", result.get("risk_factors") or []),
        ("What to confirm", result.get("what_to_confirm") or []),
    ):
        lines.extend(["", title + ":"])
        lines.extend(f"  • {value}" for value in values) if values else lines.append("  • None")

    if evaluation:
        lines.extend([
            "",
            "OBJECTIVE FOLLOW-UP",
            f"Status: {evaluation.get('status') or '-'}",
            f"Entry observed: {evaluation.get('entry_reached_at') or '-'} at {_price(evaluation.get('entry_price'))}",
            f"T1 hit: {evaluation.get('target_1_hit_at') or '-'}",
            f"T2 hit: {evaluation.get('target_2_hit_at') or '-'}",
            f"Stop hit: {evaluation.get('stop_hit_at') or '-'}",
            f"Max return after entry: {_pct(evaluation.get('max_return_pct'))}",
            f"Max drawdown after entry: {_pct(evaluation.get('max_drawdown_pct'))}",
        ])
    return "\n".join(lines)


class TradePlanWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, *, snapshot: dict[str, Any], image_path: str, model: str, api_key: str) -> None:
        super().__init__()
        self.snapshot = snapshot
        self.image_path = image_path
        self.model = model
        self.api_key = api_key

    def run(self) -> None:
        try:
            result = analyze_trade_plan(
                self.snapshot,
                image_path=self.image_path,
                api_key=self.api_key,
                model=self.model,
            )
            self.completed.emit(result)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class TradeTickerMemoryPage(stats.market.cal.CalibrationTickerMemoryPage):
    def __init__(self, repo: stats.market.cal.ai.base.DashboardRepository) -> None:
        super().__init__(repo)
        self.trade_store = TradePlanStore(repo.database_path)
        self.trade_settings = QSettings("TrendVisionAI", "TrendVisionAI")
        self._trade_worker: TradePlanWorker | None = None
        self._trade_snapshot: dict[str, Any] | None = None
        self._chart_path = ""

        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 13, 16, 13)
        layout.setSpacing(8)

        top = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("Trade Plan Experiment v1")
        title.setStyleSheet("font-size: 12pt; font-weight: 600;")
        subtitle = QLabel(
            "Attach the current chart screenshot. OpenAI receives the image plus the ticker's TrendVision evidence and latest Alpaca measurements. Potential entry/stop/targets are experimental and are automatically measured afterward; you make every trade decision manually."
        )
        subtitle.setObjectName("muted")
        subtitle.setWordWrap(True)
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        top.addLayout(title_box, 1)
        self.trade_analyze = QPushButton("Analyze Trade Plan")
        self.trade_analyze.setObjectName("primary")
        self.trade_analyze.clicked.connect(self.analyze_trade_plan)
        top.addWidget(self.trade_analyze)
        layout.addLayout(top)

        image_row = QHBoxLayout()
        choose = QPushButton("Choose screenshot")
        choose.setObjectName("secondary")
        choose.clicked.connect(self.choose_screenshot)
        paste = QPushButton("Paste screenshot")
        paste.setObjectName("secondary")
        paste.clicked.connect(self.paste_screenshot)
        clear = QPushButton("Clear")
        clear.setObjectName("secondary")
        clear.clicked.connect(self.clear_screenshot)
        self.chart_label = QLabel("No chart screenshot attached.")
        self.chart_label.setObjectName("muted")
        image_row.addWidget(choose)
        image_row.addWidget(paste)
        image_row.addWidget(clear)
        image_row.addWidget(self.chart_label, 1)
        layout.addLayout(image_row)

        self.chart_preview = QLabel("Paste or choose a PNG/JPG/WEBP chart screenshot.")
        self.chart_preview.setObjectName("muted")
        self.chart_preview.setAlignment(Qt.AlignCenter)
        self.chart_preview.setMinimumHeight(90)
        self.chart_preview.setMaximumHeight(190)
        layout.addWidget(self.chart_preview)

        self.trade_status = QLabel(
            "Open a HIGH ATTENTION ticker, attach its current chart, then run the experimental trade-plan review."
        )
        self.trade_status.setObjectName("muted")
        self.trade_status.setWordWrap(True)
        layout.addWidget(self.trade_status)

        self.trade_text = QTextEdit()
        self.trade_text.setReadOnly(True)
        self.trade_text.setMaximumHeight(430)
        self.trade_text.setPlaceholderText("No trade-plan experiment saved for this ticker yet.")
        layout.addWidget(self.trade_text)

        note = QLabel(
            "This is calibration, not a proven trade alert yet. Exact market numbers come from Alpaca; the screenshot is used for visual structure. No brokerage order is ever sent."
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        layout.addWidget(note)

        root = self.layout()
        if root is not None:
            root.addWidget(card)

    def load_ticker(self, ticker: str) -> None:
        super().load_ticker(ticker)
        self.clear_screenshot()
        self._load_latest_trade_plan()

    def refresh(self) -> None:
        super().refresh()
        self._refresh_trade_followup()

    def _image_dir(self) -> Path:
        directory = Path("data") / "trade_plan_images"
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def _new_image_path(self, suffix: str = ".png") -> Path:
        ticker = self.current_ticker or "chart"
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        return self._image_dir() / f"{ticker}_{stamp}{suffix.lower()}"

    def choose_screenshot(self) -> None:
        source, _filter = QFileDialog.getOpenFileName(
            self,
            "Choose current chart screenshot",
            str(Path.home()),
            "Images (*.png *.jpg *.jpeg *.webp *.gif)",
        )
        if not source:
            return
        source_path = Path(source)
        destination = self._new_image_path(source_path.suffix or ".png")
        try:
            shutil.copy2(source_path, destination)
        except OSError as exc:
            self.trade_status.setText(f"Could not copy screenshot: {exc}")
            return
        self._set_chart(str(destination))

    def paste_screenshot(self) -> None:
        image = QApplication.clipboard().image()
        if image.isNull():
            self.trade_status.setText("Clipboard does not currently contain an image.")
            return
        destination = self._new_image_path(".png")
        if not image.save(str(destination), "PNG"):
            self.trade_status.setText("Could not save clipboard image.")
            return
        self._set_chart(str(destination))

    def _set_chart(self, path: str) -> None:
        self._chart_path = path
        self.chart_label.setText(f"Attached: {Path(path).name}")
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self.chart_preview.setText("Image attached, but preview could not be rendered.")
        else:
            self.chart_preview.setPixmap(
                pixmap.scaled(720, 180, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
        self.trade_status.setText("Chart attached. Analyze when the screenshot represents the current setup.")

    def clear_screenshot(self) -> None:
        self._chart_path = ""
        self.chart_label.setText("No chart screenshot attached.")
        self.chart_preview.clear()
        self.chart_preview.setText("Paste or choose a PNG/JPG/WEBP chart screenshot.")

    def analyze_trade_plan(self) -> None:
        ticker = self.current_ticker.upper().strip()
        if not ticker:
            self.trade_status.setText("Open a ticker first.")
            return
        if not self._chart_path:
            self.trade_status.setText("Attach the current chart screenshot first.")
            return
        if self._trade_worker is not None and self._trade_worker.isRunning():
            return

        api_key = get_api_key()
        if not api_key:
            self.trade_status.setText("OpenAI API key is not configured under Listener & System.")
            return

        state = self.repo.ticker_state(ticker)
        convergence = self.repo.convergence(ticker, 30)
        if state is None or not convergence.get("events"):
            self.trade_status.setText("No recent TrendVision scanner evidence is available for this ticker.")
            return

        trendvision = build_review_snapshot(
            ticker=ticker,
            state=state,
            convergence=convergence,
            attention=asdict(evaluate_attention(convergence)),
        )
        snapshot = build_trade_plan_snapshot(
            database_path=self.repo.database_path,
            trendvision_snapshot=trendvision,
            latest_ai_review=self.review_store.latest(ticker),
        )
        market = snapshot.get("alpaca_market_context") or {}
        if not market.get("available"):
            self.trade_status.setText(
                "No current Alpaca sample is available yet. Wait for Market Tracking to capture the HIGH ATTENTION ticker, then try again."
            )
            return

        model = str(self.trade_settings.value("openai/model", DEFAULT_MODEL) or DEFAULT_MODEL).strip()
        self._trade_snapshot = snapshot
        self.trade_analyze.setEnabled(False)
        self.trade_analyze.setText("Analyzing chart...")
        self.trade_status.setText(
            f"Analyzing {ticker} with {model}: TrendVision + Alpaca + chart screenshot."
        )
        worker = TradePlanWorker(
            snapshot=snapshot,
            image_path=self._chart_path,
            model=model,
            api_key=api_key,
        )
        self._trade_worker = worker
        worker.completed.connect(self._trade_completed)
        worker.failed.connect(self._trade_failed)
        worker.finished.connect(self._trade_finished)
        worker.start()

    def _trade_completed(self, result: TradePlanResult) -> None:
        snapshot = self._trade_snapshot or {}
        plan_id = self.trade_store.save(result, snapshot, self._chart_path)
        evaluation = self.trade_store.evaluate(plan_id) or {}
        self.trade_text.setPlainText(_format_plan(result.to_dict(), evaluation))
        self.trade_status.setText(
            f"Trade-plan experiment #{plan_id} saved for {result.ticker}. Decision: {result.decision}. Follow-up: {evaluation.get('status') or 'waiting'}."
        )

    def _trade_failed(self, message: str) -> None:
        self.trade_status.setText("Trade-plan AI review failed. The screenshot and market tracking were not changed.")
        self.trade_text.setPlainText(message)

    def _trade_finished(self) -> None:
        self.trade_analyze.setEnabled(True)
        self.trade_analyze.setText("Analyze Trade Plan")
        if self._trade_worker is not None:
            self._trade_worker.deleteLater()
        self._trade_worker = None
        self._trade_snapshot = None

    def _load_latest_trade_plan(self) -> None:
        if not self.current_ticker:
            return
        latest = self.trade_store.latest(self.current_ticker)
        if latest is None:
            self.trade_text.clear()
            return
        evaluation = self.trade_store.evaluate(int(latest["id"])) or latest.get("evaluation") or {}
        self.trade_text.setPlainText(_format_plan(latest.get("result") or {}, evaluation))
        self.trade_status.setText(
            f"Showing trade-plan experiment #{latest['id']} for {self.current_ticker}. Follow-up: {evaluation.get('status') or 'waiting'}."
        )

    def _refresh_trade_followup(self) -> None:
        if not self.current_ticker:
            return
        latest = self.trade_store.latest(self.current_ticker)
        if latest is None:
            return
        evaluation = self.trade_store.evaluate(int(latest["id"])) or {}
        if self._trade_worker is None or not self._trade_worker.isRunning():
            self.trade_text.setPlainText(_format_plan(latest.get("result") or {}, evaluation))


class TradePlansPage(QWidget):
    ticker_requested = Signal(str)

    def __init__(self, database_path) -> None:
        super().__init__()
        self.store = TradePlanStore(database_path)
        self._last_refresh = 0.0

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)

        title = QLabel("Trade Plan Experiments")
        title.setObjectName("pageTitle")
        subtitle = QLabel(
            "Saved screenshot-enhanced AI plans and their objective Alpaca follow-up. These experiments are how we test whether proposed entry/stop/targets are actually useful before calling them trade alerts."
        )
        subtitle.setObjectName("muted")
        subtitle.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(subtitle)

        metrics = QHBoxLayout()
        self.total_card = stats.market.cal.ai.base.MetricCard("Plans")
        self.potential_card = stats.market.cal.ai.base.MetricCard("Potential trades")
        self.t1_card = stats.market.cal.ai.base.MetricCard("T1 reached")
        self.t2_card = stats.market.cal.ai.base.MetricCard("T2 reached")
        self.stop_card = stats.market.cal.ai.base.MetricCard("Stopped first")
        for card in (self.total_card, self.potential_card, self.t1_card, self.t2_card, self.stop_card):
            metrics.addWidget(card)
        root.addLayout(metrics)

        self.table = QTableWidget(0, 11)
        self.table.setHorizontalHeaderLabels([
            "Created", "Ticker", "Decision", "Risk", "Entry", "Stop", "T1", "T2", "R/R T1", "R/R T2", "Objective follow-up"
        ])
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        for column in range(10):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(10, QHeaderView.Stretch)
        self.table.cellDoubleClicked.connect(self._open_ticker)
        root.addWidget(self.table, 1)

        note = QLabel(
            "Evaluation uses stored Alpaca sampled trade prices after the plan timestamp. It records whether the entry zone was observed and whether stop/T1/T2 were subsequently observed; it does not assume that you personally entered a trade."
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        root.addWidget(note)

    def _open_ticker(self, row: int, _column: int) -> None:
        item = self.table.item(row, 1)
        if item and item.text():
            self.ticker_requested.emit(item.text())

    def refresh(self) -> None:
        now = time.monotonic()
        if now - self._last_refresh >= 10.0:
            try:
                self.store.refresh_evaluations(limit=200)
            except Exception:
                pass
            self._last_refresh = now

        plans = self.store.list_plans(limit=200)
        self.total_card.set_value(len(plans))
        self.potential_card.set_value(sum(1 for p in plans if p.get("decision") == "POTENTIAL TRADE"))
        statuses = [str(p.get("evaluation_status") or "") for p in plans]
        self.t1_card.set_value(sum(1 for s in statuses if s in {"TARGET 1 ONLY", "TARGET 1 HIT / OPEN", "TARGET 1 THEN STOP", "TARGET 2 HIT"}))
        self.t2_card.set_value(sum(1 for s in statuses if s == "TARGET 2 HIT"))
        self.stop_card.set_value(sum(1 for s in statuses if s == "STOP HIT FIRST"))

        self.table.setRowCount(len(plans))
        for row, plan in enumerate(plans):
            result = plan.get("result") or {}
            entry = (
                f"{_price(plan.get('entry_low'))}–{_price(plan.get('entry_high'))}"
                if plan.get("entry_low") is not None else "-"
            )
            values = [
                stats.market.cal.ai.base._display_time(str(plan.get("created_at") or "")),
                plan.get("ticker") or "-",
                plan.get("decision") or "-",
                plan.get("risk_level") or "-",
                entry,
                _price(plan.get("stop_loss")),
                _price(plan.get("target_1")),
                _price(plan.get("target_2")),
                _rr(result.get("risk_reward_target_1")),
                _rr(result.get("risk_reward_target_2")),
                plan.get("evaluation_status") or "WAITING",
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                if column >= 2:
                    cell.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row, column, cell)


# Swap the Ticker Memory page before the inherited MainWindow constructs it.
stats.market.cal.ai.base.TickerMemoryPage = TradeTickerMemoryPage


class TradeMainWindow(stats.StatsMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.trade_store = TradePlanStore(self.config.database_path)
        self.trade_plans_page = TradePlansPage(self.config.database_path)
        page_index = self.stack.addWidget(self.trade_plans_page)

        button = QPushButton("Trade Plan Experiments")
        button.setObjectName("nav")
        button.setCheckable(True)
        button.clicked.connect(lambda _checked=False, i=page_index: self.navigate(i))
        sidebar_layout = self.nav_buttons[-1].parentWidget().layout()
        insert_at = sidebar_layout.indexOf(self.nav_buttons[-1]) + 1
        sidebar_layout.insertWidget(insert_at, button)
        self.nav_buttons.append(button)
        self.trade_plans_page.ticker_requested.connect(self.open_ticker)
        self.market_controller.data_updated.connect(self._refresh_trade_evaluations)

    def _refresh_trade_evaluations(self) -> None:
        try:
            self.trade_store.refresh_evaluations(limit=200)
            self.trade_plans_page.refresh()
        except Exception:
            pass


stats.market.cal.ai.base.MainWindow = TradeMainWindow


def main() -> int:
    return stats.market.cal.ai.base.main()


if __name__ == "__main__":
    raise SystemExit(main())
