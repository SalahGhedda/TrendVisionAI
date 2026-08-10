from __future__ import annotations

import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSettings, QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStyle,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import desktop_ui_trade_stats as stats_ui
from .ai_review import AIReviewStore, DEFAULT_MODEL, build_review_snapshot, get_api_key
from .attention import evaluate_attention
from .auto_chart import (
    AUTO_CHART_LOOKBACK_MINUTES,
    AlpacaRecentBarsClient,
    AutoChartError,
    render_candles_png,
)
from .automatic_trade_plan import AUTO_PLAN_SOURCE, analyze_automatic_trade_plan
from .live_pipeline import (
    LivePipelineStore,
    final_trade_alert_gate,
    qualification_summary,
    regular_session_state,
)
from .market_data import get_alpaca_credentials
from .qualification import CandidateQualificationEngine
from .trade_plan_calibration_v3 import build_trade_plan_snapshot_v3
from .trade_plans import TradePlanResult, TradePlanStore


base = stats_ui.base


def _price(value: Any) -> str:
    try:
        return f"${float(value):.4f}" if value is not None else "-"
    except (TypeError, ValueError):
        return "-"


def _display_time(value: Any) -> str:
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed.astimezone().strftime("%H:%M:%S")
    except (TypeError, ValueError):
        return str(value or "-")


class AutomaticTradePlanWorker(QThread):
    completed = Signal(object, object, str)
    failed = Signal(str)

    def __init__(
        self,
        *,
        ticker: str,
        session_id: int,
        snapshot: dict[str, Any],
        model: str,
        openai_api_key: str,
        alpaca_key_id: str,
        alpaca_secret: str,
        feed: str,
    ) -> None:
        super().__init__()
        self.ticker = ticker
        self.session_id = int(session_id)
        self.snapshot = snapshot
        self.model = model
        self.openai_api_key = openai_api_key
        self.alpaca_key_id = alpaca_key_id
        self.alpaca_secret = alpaca_secret
        self.feed = feed

    def run(self) -> None:
        try:
            client = AlpacaRecentBarsClient(
                self.alpaca_key_id,
                self.alpaca_secret,
                feed=self.feed,
            )
            bars = client.fetch_recent_bars(
                self.ticker,
                lookback_minutes=AUTO_CHART_LOOKBACK_MINUTES,
            )
            chart_dir = Path("data") / "auto_trade_charts"
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            chart_path = chart_dir / f"{self.ticker}_{self.session_id}_{stamp}.png"
            render_candles_png(
                ticker=self.ticker,
                bars=bars,
                destination=chart_path,
                feed=self.feed,
            )
            result = analyze_automatic_trade_plan(
                self.snapshot,
                image_path=chart_path,
                bars=bars,
                api_key=self.openai_api_key,
                model=self.model,
            )
            self.completed.emit(result, bars, str(chart_path))
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class LiveTradePipelinePage(QWidget):
    def __init__(self, store: LivePipelineStore) -> None:
        super().__init__()
        self.store = store

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)

        title = QLabel("Live Trade Pipeline")
        title.setObjectName("pageTitle")
        subtitle = QLabel(
            "Automatic HIGH ATTENTION → qualification → chart context → Trade Plan → hard alert gate. "
            "The pipeline can generate calibration plans before qualification is mature, but it sends a trade alert only after the historical gate and all deterministic blockers pass. No brokerage order is sent."
        )
        subtitle.setObjectName("muted")
        subtitle.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(subtitle)

        metrics = QHBoxLayout()
        self.qualified_card = base.MetricCard("Qualified candidates")
        self.auto_card = base.MetricCard("Automatic plans")
        self.alert_card = base.MetricCard("Trade alerts")
        self.blocked_card = base.MetricCard("Blocked alerts")
        for card in (
            self.qualified_card,
            self.auto_card,
            self.alert_card,
            self.blocked_card,
        ):
            metrics.addWidget(card)
        root.addLayout(metrics)

        self.status = QLabel(
            "During regular market hours, each new HIGH ATTENTION tracking session can receive one automatic calibration Trade Plan when fresh Alpaca context is available."
        )
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["Time", "Ticker", "Stage", "Status", "Session", "Plan", "Details"]
        )
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        for column in range(6):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.Stretch)
        root.addWidget(self.table, 1)

        note = QLabel(
            "FINAL TRADE ALERT requires: regular session, EXPERIMENTALLY QUALIFIED history, fresh trade/quote context, fresh known spread below the configured guardrail, coherent Entry/SL/T1/T2, acceptable chart/risk state, and duplicate protection."
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        root.addWidget(note)

    def set_status(self, text: str) -> None:
        self.status.setText(text)

    def refresh(self) -> None:
        events = self.store.list_events(limit=200)
        self.qualified_card.set_value(sum(1 for row in events if row.get("stage") == "QUALIFIED_CANDIDATE"))
        self.auto_card.set_value(sum(1 for row in events if row.get("stage") == "AUTO_PLAN"))
        self.alert_card.set_value(sum(1 for row in events if row.get("stage") == "FINAL_TRADE_ALERT"))
        self.blocked_card.set_value(sum(1 for row in events if row.get("stage") == "ALERT_BLOCKED"))

        self.table.setRowCount(len(events))
        for row_index, event in enumerate(events):
            payload = event.get("payload") or {}
            details = payload.get("reason") or payload.get("summary") or payload.get("blockers") or "-"
            if isinstance(details, list):
                details = ", ".join(str(value) for value in details)
            values = [
                _display_time(event.get("created_at")),
                event.get("ticker") or "-",
                event.get("stage") or "-",
                event.get("status") or "-",
                event.get("session_id") or "-",
                event.get("plan_id") or "-",
                details,
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                if column in {0, 1, 2, 3, 4, 5}:
                    cell.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row_index, column, cell)


PreviousMainWindow = base.MainWindow


class LivePipelineMainWindow(PreviousMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.live_store = LivePipelineStore(self.config.database_path)
        self.live_qualification = CandidateQualificationEngine(self.config.database_path)
        self.live_trade_store = TradePlanStore(self.config.database_path)
        self.live_review_store = AIReviewStore(self.config.database_path)
        self.live_settings = QSettings("TrendVisionAI", "TrendVisionAI")
        self._auto_worker: AutomaticTradePlanWorker | None = None
        self._auto_context: dict[str, Any] | None = None
        self._last_pipeline_scan = 0.0

        self.live_page = LiveTradePipelinePage(self.live_store)
        live_index = self.stack.addWidget(self.live_page)
        live_button = QPushButton("Live Trade Pipeline")
        live_button.setObjectName("nav")
        live_button.setCheckable(True)
        live_button.clicked.connect(lambda _checked=False, i=live_index: self.navigate(i))
        sidebar_layout = self.nav_buttons[-1].parentWidget().layout()
        insert_at = sidebar_layout.indexOf(self.nav_buttons[-1]) + 1
        sidebar_layout.insertWidget(insert_at, live_button)
        self.nav_buttons.append(live_button)

        self._tray = QSystemTrayIcon(self)
        self._tray.setIcon(self.style().standardIcon(QStyle.SP_ComputerIcon))
        self._tray.setToolTip("TrendVisionAI")
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._tray.show()

        self.pipeline_timer = QTimer(self)
        self.pipeline_timer.setInterval(8000)
        self.pipeline_timer.timeout.connect(self._run_live_pipeline)
        self.pipeline_timer.start()
        try:
            self.market_controller.data_updated.connect(
                lambda: QTimer.singleShot(600, self._run_live_pipeline)
            )
        except Exception:
            pass
        QTimer.singleShot(1200, self._run_live_pipeline)

    def _notify(self, title: str, message: str, *, urgent: bool = False) -> None:
        if QSystemTrayIcon.isSystemTrayAvailable():
            icon = QSystemTrayIcon.Critical if urgent else QSystemTrayIcon.Information
            self._tray.showMessage(title, message, icon, 12000)
        if urgent:
            QApplication.beep()

    def _record_qualified(self, ticker: str, session_id: int, qualification: dict[str, Any]) -> None:
        created, _event = self.live_store.record_once(
            dedup_key=f"qualified:v1:{session_id}",
            ticker=ticker,
            session_id=session_id,
            stage="QUALIFIED_CANDIDATE",
            status="EXPERIMENTALLY QUALIFIED",
            payload={
                "reason": qualification.get("reason"),
                "positive_patterns": [
                    row.get("pattern") for row in (qualification.get("positive_patterns") or [])[:5]
                ],
            },
        )
        if created:
            self._notify(
                f"TrendVisionAI qualified candidate: {ticker}",
                "Historical setup evidence passed. Automatic final Trade Plan review is running when fresh market context is available.",
            )

    def _build_live_snapshot(self, ticker: str) -> dict[str, Any] | None:
        state = self.repo.ticker_state(ticker)
        convergence = self.repo.convergence(ticker, 30)
        if state is None or not convergence.get("events"):
            return None
        trendvision = build_review_snapshot(
            ticker=ticker,
            state=state,
            convergence=convergence,
            attention=asdict(evaluate_attention(convergence)),
        )
        return build_trade_plan_snapshot_v3(
            database_path=self.config.database_path,
            trendvision_snapshot=trendvision,
            latest_ai_review=self.live_review_store.latest(ticker),
        )

    def _start_auto_plan(
        self,
        *,
        ticker: str,
        session_id: int,
        qualification: dict[str, Any],
    ) -> bool:
        if self._auto_worker is not None and self._auto_worker.isRunning():
            return False

        snapshot = self._build_live_snapshot(ticker)
        if snapshot is None:
            return False
        market = snapshot.get("alpaca_market_context") or {}
        if not market.get("current_context_usable"):
            return False

        openai_key = get_api_key()
        alpaca = get_alpaca_credentials()
        if not openai_key or not alpaca:
            self.live_page.set_status(
                "Automatic Trade Plan is waiting for configured OpenAI and Alpaca credentials."
            )
            return False

        model = str(
            self.live_settings.value("openai/model", DEFAULT_MODEL) or DEFAULT_MODEL
        ).strip()
        feed = str(market.get("feed") or "iex").strip().lower()
        self._auto_context = {
            "ticker": ticker,
            "session_id": int(session_id),
            "qualification": qualification,
            "snapshot": snapshot,
        }
        self.live_page.set_status(
            f"Automatic Trade Plan running for {ticker}: TrendVision + fresh Alpaca + generated 1-minute chart context."
        )
        worker = AutomaticTradePlanWorker(
            ticker=ticker,
            session_id=session_id,
            snapshot=snapshot,
            model=model,
            openai_api_key=openai_key,
            alpaca_key_id=alpaca[0],
            alpaca_secret=alpaca[1],
            feed=feed,
        )
        self._auto_worker = worker
        worker.completed.connect(self._auto_plan_completed)
        worker.failed.connect(self._auto_plan_failed)
        worker.finished.connect(self._auto_plan_finished)
        worker.start()
        return True

    def _auto_plan_completed(
        self,
        result: TradePlanResult,
        bars: list[dict[str, Any]],
        chart_path: str,
    ) -> None:
        context = self._auto_context or {}
        ticker = str(context.get("ticker") or result.ticker).upper()
        session_id = int(context.get("session_id") or 0)
        qualification = context.get("qualification") or {}
        snapshot = dict(context.get("snapshot") or {})
        snapshot["plan_source"] = AUTO_PLAN_SOURCE
        snapshot["automatic_chart_context"] = {
            "source": "Alpaca recent 1-minute bars",
            "bar_count": len(bars),
            "bars": bars,
            "chart_path": chart_path,
        }
        snapshot["qualification_at_plan_time"] = qualification

        plan_id = self.live_trade_store.save(result, snapshot, chart_path)
        evaluation = self.live_trade_store.evaluate(plan_id) or {}
        self.live_store.record_once(
            dedup_key=f"auto_plan:v1:{session_id}",
            ticker=ticker,
            session_id=session_id,
            stage="AUTO_PLAN",
            status=result.decision,
            plan_id=plan_id,
            payload={
                "summary": result.summary,
                "evaluation_status": evaluation.get("status"),
                "source": AUTO_PLAN_SOURCE,
            },
        )

        if result.decision == "POTENTIAL TRADE" and qualification.get("status") == "EXPERIMENTALLY QUALIFIED":
            gate = final_trade_alert_gate(
                qualification=qualification,
                plan=result.to_dict(),
                snapshot=snapshot,
            )
            if gate.get("allowed"):
                created, _event = self.live_store.record_once(
                    dedup_key=f"final_trade_alert:v1:{session_id}",
                    ticker=ticker,
                    session_id=session_id,
                    stage="FINAL_TRADE_ALERT",
                    status="READY FOR MANUAL DECISION",
                    plan_id=plan_id,
                    payload={
                        "summary": result.summary,
                        "entry_low": result.entry_low,
                        "entry_high": result.entry_high,
                        "stop_loss": result.stop_loss,
                        "target_1": result.target_1,
                        "target_2": result.target_2,
                        "risk_reward_target_1": result.risk_reward_target_1,
                        "risk_reward_target_2": result.risk_reward_target_2,
                        "qualification": qualification_summary(qualification),
                    },
                )
                if created:
                    self._notify(
                        f"🚨 TrendVisionAI TRADE ALERT — {ticker}",
                        (
                            f"Entry {_price(result.entry_low)}–{_price(result.entry_high)} | "
                            f"Stop {_price(result.stop_loss)} | T1 {_price(result.target_1)} | "
                            f"T2 {_price(result.target_2)}. Manual decision only."
                        ),
                        urgent=True,
                    )
            else:
                self.live_store.record_once(
                    dedup_key=f"alert_blocked:v1:{session_id}:{plan_id}",
                    ticker=ticker,
                    session_id=session_id,
                    stage="ALERT_BLOCKED",
                    status="HARD GATE BLOCKED",
                    plan_id=plan_id,
                    payload={"blockers": gate.get("blockers") or []},
                )

        self.live_page.refresh()
        self.live_page.set_status(
            f"Automatic Trade Plan #{plan_id} saved for {ticker}: {result.decision}. "
            + (
                "Final alert gate evaluated."
                if qualification.get("status") == "EXPERIMENTALLY QUALIFIED"
                else "Calibration-only plan; historical qualification is not mature/passing yet."
            )
        )

    def _auto_plan_failed(self, message: str) -> None:
        context = self._auto_context or {}
        ticker = str(context.get("ticker") or "?").upper()
        session_id = int(context.get("session_id") or 0)
        minute_bucket = int(time.time() // 60)
        self.live_store.record_once(
            dedup_key=f"auto_plan_error:v1:{session_id}:{minute_bucket}",
            ticker=ticker,
            session_id=session_id or None,
            stage="AUTO_PLAN_ERROR",
            status="RETRY LATER",
            payload={"reason": message},
        )
        self.live_page.set_status(
            f"Automatic Trade Plan for {ticker} did not run successfully and will be eligible to retry later: {message}"
        )
        self.live_page.refresh()

    def _auto_plan_finished(self) -> None:
        if self._auto_worker is not None:
            self._auto_worker.deleteLater()
        self._auto_worker = None
        self._auto_context = None

    def _run_live_pipeline(self) -> None:
        now = time.monotonic()
        if now - self._last_pipeline_scan < 3.0:
            return
        self._last_pipeline_scan = now
        if self._auto_worker is not None and self._auto_worker.isRunning():
            return

        session_gate = regular_session_state()
        if not session_gate.get("open"):
            self.live_page.set_status(
                "Regular market session is closed. Automatic Trade Plans and trade alerts are paused; scanner capture/calibration history remain available."
            )
            return

        try:
            attention = self.repo.attention_list(30, limit=100)
        except Exception as exc:
            self.live_page.set_status(f"Live pipeline could not read attention list: {type(exc).__name__}: {exc}")
            return

        high = [item for item in attention if item.tier == "HIGH ATTENTION"]
        if not high:
            self.live_page.set_status("Regular market session open; waiting for a HIGH ATTENTION candidate.")
            return

        for item in high:
            session = self.live_qualification.latest_session_for_ticker(item.ticker)
            if session is None:
                continue
            session_id = int(session["id"])
            qualification = self.live_qualification.qualify_session(session_id)
            if qualification.get("status") == "EXPERIMENTALLY QUALIFIED":
                self._record_qualified(item.ticker, session_id, qualification)

            existing = self.live_store.existing_trade_plan_for_session(session_id)
            if existing is not None:
                continue

            if self._start_auto_plan(
                ticker=item.ticker,
                session_id=session_id,
                qualification=qualification,
            ):
                break

        self.live_page.refresh()


base.MainWindow = LivePipelineMainWindow


def main() -> int:
    return stats_ui.main()


if __name__ == "__main__":
    raise SystemExit(main())
