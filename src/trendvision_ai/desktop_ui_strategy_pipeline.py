from __future__ import annotations

import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import desktop_ui_live_pipeline as live
from .ai_review import DEFAULT_MODEL, build_review_snapshot, get_api_key
from .attention import evaluate_attention
from .auto_chart import AUTO_CHART_LIMIT, AlpacaRecentBarsClient, render_candles_png
from .automatic_trade_plan import AUTO_PLAN_SOURCE, analyze_automatic_trade_plan
from .live_pipeline import LivePipelineStore, final_trade_alert_gate, qualification_summary, regular_session_state
from .market_data import get_alpaca_credentials
from .strategy_library_v2 import detect_known_setups, strategy_catalog
from .trade_plan_calibration_v3 import build_trade_plan_snapshot_v3
from .trade_plans import TradePlanResult


base = live.base
STRATEGY_SCAN_COOLDOWN_SECONDS = 30.0
MAX_AUTO_STRATEGIES_PER_SESSION = 8


class StrategyTradePlanWorker(QThread):
    completed = Signal(object, object, str, object)
    failed = Signal(str)
    no_setup = Signal(object)
    strategy_found = Signal(object)
    duplicate = Signal(object)

    def __init__(
        self,
        *,
        database_path: str,
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
        self.database_path = database_path
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
            momentum_bars = client.fetch_momentum_bars(self.ticker)
            strategy_context = detect_known_setups(momentum_bars)
            if not strategy_context.get("recognized"):
                self.no_setup.emit(strategy_context)
                return

            store = LivePipelineStore(self.database_path)
            auto_events = [
                event
                for event in store.list_events(limit=1000)
                if int(event.get("session_id") or 0) == self.session_id
                and event.get("stage") == "AUTO_PLAN"
            ]
            if len(auto_events) >= MAX_AUTO_STRATEGIES_PER_SESSION:
                self.duplicate.emit(
                    {
                        "reason": f"Automatic setup-instance plan cap reached ({MAX_AUTO_STRATEGIES_PER_SESSION} per tracking session).",
                        "strategy_context": strategy_context,
                    }
                )
                return

            selected = None
            for match in strategy_context.get("matches") or []:
                strategy_id = str(match.get("strategy_id") or "").strip()
                instance_key = str(match.get("instance_key") or strategy_id).strip()
                if not strategy_id:
                    continue
                existing = store.existing_trade_plan_for_session(
                    self.session_id,
                    strategy_id=strategy_id,
                    setup_instance_key=instance_key,
                )
                if existing is None:
                    selected = match
                    break
            if selected is None:
                self.duplicate.emit(
                    {
                        "reason": "All currently recognized setup instances for this session have already received an automatic Trade Plan. A materially new break/retest/reclaim can be analyzed later.",
                        "strategy_context": strategy_context,
                    }
                )
                return

            strategy_context = dict(strategy_context)
            strategy_context["primary"] = dict(selected)
            strategy_context["recognized"] = True
            strategy_context["summary"] = (
                f"Primary setup: {selected.get('name') or selected.get('strategy_id')} "
                f"(score {selected.get('score')}/100, instance {selected.get('instance_key') or '-'})."
            )
            self.strategy_found.emit(strategy_context)

            chart_bars = momentum_bars[-AUTO_CHART_LIMIT:]
            chart_dir = Path("data") / "auto_trade_charts"
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            chart_path = chart_dir / f"{self.ticker}_{self.session_id}_{selected.get('strategy_id')}_{stamp}.png"
            render_candles_png(
                ticker=self.ticker,
                bars=chart_bars,
                destination=chart_path,
                feed=self.feed,
            )
            result = analyze_automatic_trade_plan(
                self.snapshot,
                image_path=chart_path,
                bars=chart_bars,
                strategy_context=strategy_context,
                api_key=self.openai_api_key,
                model=self.model,
            )
            self.completed.emit(result, chart_bars, str(chart_path), strategy_context)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class StrategyLiveTradePipelinePage(live.LiveTradePipelinePage):
    def __init__(self, store: LivePipelineStore) -> None:
        super().__init__(store)
        for label in self.findChildren(QLabel):
            text = label.text()
            if text == "Qualified candidates":
                label.setText("Strategy matches")
            elif text.startswith("Automatic HIGH ATTENTION"):
                label.setText(
                    "Automatic HIGH ATTENTION → momentum-aware known setup recognition → Trade Plan → calibration validator → hard alert gate. "
                    "Premarket levels and recent volatility are context; actionable alerts still require the regular session. A later materially new setup instance can be reviewed again."
                )
            elif text.startswith("FINAL TRADE ALERT requires"):
                label.setText(
                    "FINAL TRADE ALERT requires: a recognized Strategy Library setup, regular session, fresh market context, acceptable spread, coherent Entry/SL/T1/T2, safe chart/risk state, and no mature negative calibration veto. Immature history alone does not block a known setup."
                )

    def refresh(self) -> None:
        events = self.store.list_events(limit=200)
        self.qualified_card.set_value(sum(1 for row in events if row.get("stage") == "STRATEGY_MATCH"))
        self.auto_card.set_value(sum(1 for row in events if row.get("stage") == "AUTO_PLAN"))
        self.alert_card.set_value(sum(1 for row in events if row.get("stage") == "FINAL_TRADE_ALERT"))
        self.blocked_card.set_value(sum(1 for row in events if row.get("stage") == "ALERT_BLOCKED"))

        self.table.setRowCount(len(events))
        for row_index, event in enumerate(events):
            payload = event.get("payload") or {}
            details = (
                payload.get("reason")
                or payload.get("strategy_name")
                or payload.get("summary")
                or payload.get("blockers")
                or "-"
            )
            if isinstance(details, list):
                details = ", ".join(str(value) for value in details)
            values = [
                live._display_time(event.get("created_at")),
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


class StrategyLibraryPage(QWidget):
    def __init__(self, store: LivePipelineStore) -> None:
        super().__init__()
        self.store = store
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)

        title = QLabel("Strategy Library")
        title.setObjectName("pageTitle")
        subtitle = QLabel(
            "Momentum-aware deterministic intraday setup families. Version 2 adds premarket-high structure, bull-flag continuation, VWAP pullback/hold, volatility-aware anti-chase limits, and setup-instance identities so a later new entry window can be reviewed again."
        )
        subtitle.setObjectName("muted")
        subtitle.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(subtitle)

        self.catalog = QTableWidget(0, 5)
        self.catalog.setHorizontalHeaderLabels(
            ["Strategy", "ID", "Detection idea", "Entry framework", "Invalidation framework"]
        )
        self.catalog.setAlternatingRowColors(True)
        self.catalog.setEditTriggers(QTableWidget.NoEditTriggers)
        self.catalog.verticalHeader().setVisible(False)
        header = self.catalog.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        for column in (2, 3, 4):
            header.setSectionResizeMode(column, QHeaderView.Stretch)
        root.addWidget(self.catalog, 1)

        recent_title = QLabel("Recent recognized setup instances")
        recent_title.setStyleSheet("font-size: 12pt; font-weight: 600;")
        root.addWidget(recent_title)
        self.recent = QTableWidget(0, 5)
        self.recent.setHorizontalHeaderLabels(["Time", "Ticker", "Strategy", "Score", "Calibration"])
        self.recent.setAlternatingRowColors(True)
        self.recent.setEditTriggers(QTableWidget.NoEditTriggers)
        self.recent.verticalHeader().setVisible(False)
        recent_header = self.recent.horizontalHeader()
        for column in range(4):
            recent_header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        recent_header.setSectionResizeMode(4, QHeaderView.Stretch)
        root.addWidget(self.recent, 1)

        note = QLabel(
            "Premarket bars are context only; the final gate still requires the regular US session. Rules remain explicit and same-feed, and a recognized setup is not a claim of profitability."
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        root.addWidget(note)
        self._load_catalog()

    def _load_catalog(self) -> None:
        rows = strategy_catalog()
        self.catalog.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            values = [
                row.get("name") or "-",
                row.get("strategy_id") or "-",
                row.get("description") or "-",
                row.get("entry_framework") or "-",
                row.get("invalidation_framework") or "-",
            ]
            for column, value in enumerate(values):
                self.catalog.setItem(row_index, column, QTableWidgetItem(str(value)))

    def refresh(self) -> None:
        events = [
            event for event in self.store.list_events(limit=500)
            if event.get("stage") == "STRATEGY_MATCH"
        ][:80]
        self.recent.setRowCount(len(events))
        for row_index, event in enumerate(events):
            payload = event.get("payload") or {}
            values = [
                live._display_time(event.get("created_at")),
                event.get("ticker") or "-",
                payload.get("strategy_name") or payload.get("strategy_id") or "-",
                payload.get("score") or "-",
                payload.get("calibration_status") or "-",
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                if column != 2:
                    cell.setTextAlignment(Qt.AlignCenter)
                self.recent.setItem(row_index, column, cell)


PreviousMainWindow = base.MainWindow


class StrategyPipelineMainWindow(PreviousMainWindow):
    def __init__(self) -> None:
        self._strategy_scan_last: dict[int, float] = {}
        super().__init__()

        old_page = self.live_page
        old_index = self.stack.indexOf(old_page)
        self.stack.removeWidget(old_page)
        self.live_page = StrategyLiveTradePipelinePage(self.live_store)
        self.stack.insertWidget(old_index, self.live_page)
        old_page.deleteLater()

        self.strategy_page = StrategyLibraryPage(self.live_store)
        strategy_index = self.stack.addWidget(self.strategy_page)
        strategy_button = QPushButton("Strategy Library")
        strategy_button.setObjectName("nav")
        strategy_button.setCheckable(True)
        strategy_button.clicked.connect(lambda _checked=False, i=strategy_index: self.navigate(i))
        sidebar_layout = self.nav_buttons[-1].parentWidget().layout()
        insert_at = sidebar_layout.indexOf(self.nav_buttons[-1])
        sidebar_layout.insertWidget(insert_at, strategy_button)
        self.nav_buttons.append(strategy_button)

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
                "Strategy pipeline is waiting for configured OpenAI and Alpaca credentials."
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
            f"Scanning {ticker} with premarket context and volatility-aware known setups before any OpenAI Trade Plan request."
        )
        worker = StrategyTradePlanWorker(
            database_path=str(self.config.database_path),
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
        worker.strategy_found.connect(self._strategy_found)
        worker.no_setup.connect(self._strategy_no_setup)
        worker.duplicate.connect(self._strategy_duplicate)
        worker.completed.connect(self._auto_plan_completed)
        worker.failed.connect(self._auto_plan_failed)
        worker.finished.connect(self._auto_plan_finished)
        worker.start()
        return True

    def _strategy_found(self, strategy_context: dict[str, Any]) -> None:
        context = self._auto_context or {}
        ticker = str(context.get("ticker") or "?").upper()
        session_id = int(context.get("session_id") or 0)
        primary = strategy_context.get("primary") or {}
        strategy_id = str(primary.get("strategy_id") or "UNKNOWN")
        instance_key = str(primary.get("instance_key") or strategy_id)
        validation = self.live_qualification.validate_strategy_context(strategy_context)
        self.live_store.record_once(
            dedup_key=f"strategy_match:v2:{session_id}:{instance_key}",
            ticker=ticker,
            session_id=session_id,
            stage="STRATEGY_MATCH",
            status="KNOWN SETUP RECOGNIZED",
            payload={
                "strategy_id": strategy_id,
                "strategy_name": primary.get("name"),
                "setup_instance_key": instance_key,
                "score": primary.get("score"),
                "reason": strategy_context.get("summary"),
                "calibration_status": validation.get("status"),
                "premarket_context": strategy_context.get("premarket_context") or {},
            },
        )
        self.live_page.set_status(
            f"{ticker}: {primary.get('name') or strategy_id} recognized (score {primary.get('score')}/100). Running Terra for this setup instance."
        )
        self.live_page.refresh()
        self.strategy_page.refresh()

    def _strategy_no_setup(self, strategy_context: dict[str, Any]) -> None:
        context = self._auto_context or {}
        ticker = str(context.get("ticker") or "?").upper()
        session_id = int(context.get("session_id") or 0)
        bucket = int(time.time() // 300)
        self.live_store.record_once(
            dedup_key=f"strategy_scan:none:v2:{session_id}:{bucket}",
            ticker=ticker,
            session_id=session_id,
            stage="STRATEGY_SCAN",
            status="NO VALID SETUP",
            payload={
                "reason": strategy_context.get("summary"),
                "premarket_context": strategy_context.get("premarket_context") or {},
            },
        )
        self.live_page.set_status(
            f"{ticker} is HIGH ATTENTION, but no configured momentum setup is valid right now. No OpenAI request was used; a later new setup can still qualify."
        )
        self.live_page.refresh()

    def _strategy_duplicate(self, payload: dict[str, Any]) -> None:
        context = self._auto_context or {}
        ticker = str(context.get("ticker") or "?").upper()
        self.live_page.set_status(f"{ticker}: {payload.get('reason') or 'Current setup instance already analyzed.'}")

    def _auto_plan_completed(
        self,
        result: TradePlanResult,
        bars: list[dict[str, Any]],
        chart_path: str,
        strategy_context: dict[str, Any],
    ) -> None:
        context = self._auto_context or {}
        ticker = str(context.get("ticker") or result.ticker).upper()
        session_id = int(context.get("session_id") or 0)
        qualification = context.get("qualification") or {}
        strategy_validation = self.live_qualification.validate_strategy_context(strategy_context)
        primary = strategy_context.get("primary") or {}
        strategy_id = str(primary.get("strategy_id") or "UNKNOWN")
        strategy_name = str(primary.get("name") or strategy_id)
        instance_key = str(primary.get("instance_key") or strategy_id)

        snapshot = dict(context.get("snapshot") or {})
        snapshot["plan_source"] = AUTO_PLAN_SOURCE
        snapshot["strategy_context"] = strategy_context
        snapshot["strategy_validation"] = strategy_validation
        snapshot["automatic_chart_context"] = {
            "source": "Alpaca 04:00+ New York 1-minute context (premarket + regular session when available)",
            "bar_count": len(bars),
            "bars": bars,
            "chart_path": chart_path,
            "premarket_context": strategy_context.get("premarket_context") or {},
        }
        snapshot["qualification_at_plan_time"] = qualification

        plan_id = self.live_trade_store.save(result, snapshot, chart_path)
        evaluation = self.live_trade_store.evaluate(plan_id) or {}
        self.live_store.record_once(
            dedup_key=f"auto_plan:v3:{session_id}:{instance_key}",
            ticker=ticker,
            session_id=session_id,
            stage="AUTO_PLAN",
            status=result.decision,
            plan_id=plan_id,
            payload={
                "summary": result.summary,
                "evaluation_status": evaluation.get("status"),
                "source": AUTO_PLAN_SOURCE,
                "strategy_id": strategy_id,
                "strategy_name": strategy_name,
                "setup_instance_key": instance_key,
                "strategy_score": primary.get("score"),
                "strategy_calibration": strategy_validation.get("status"),
                "detection_calibration": qualification.get("status"),
            },
        )

        if result.decision == "POTENTIAL TRADE":
            gate = final_trade_alert_gate(
                qualification=qualification,
                plan=result.to_dict(),
                snapshot=snapshot,
                strategy_validation=strategy_validation,
            )
            if gate.get("allowed"):
                created, _event = self.live_store.record_once(
                    dedup_key=f"final_trade_alert:v3:{session_id}:{instance_key}",
                    ticker=ticker,
                    session_id=session_id,
                    stage="FINAL_TRADE_ALERT",
                    status="READY FOR MANUAL DECISION",
                    plan_id=plan_id,
                    payload={
                        "summary": result.summary,
                        "strategy_id": strategy_id,
                        "strategy_name": strategy_name,
                        "setup_instance_key": instance_key,
                        "strategy_score": primary.get("score"),
                        "entry_low": result.entry_low,
                        "entry_high": result.entry_high,
                        "stop_loss": result.stop_loss,
                        "target_1": result.target_1,
                        "target_2": result.target_2,
                        "risk_reward_target_1": result.risk_reward_target_1,
                        "risk_reward_target_2": result.risk_reward_target_2,
                        "strategy_calibration": strategy_validation.get("status"),
                        "detection_calibration": qualification_summary(qualification),
                    },
                )
                if created:
                    self._notify(
                        f"🚨 TrendVisionAI TRADE ALERT — {ticker}",
                        (
                            f"{strategy_name} | Entry {live._price(result.entry_low)}–{live._price(result.entry_high)} | "
                            f"Stop {live._price(result.stop_loss)} | T1 {live._price(result.target_1)} | "
                            f"T2 {live._price(result.target_2)}. Manual decision only."
                        ),
                        urgent=True,
                    )
            else:
                self.live_store.record_once(
                    dedup_key=f"alert_blocked:v3:{session_id}:{instance_key}:{plan_id}",
                    ticker=ticker,
                    session_id=session_id,
                    stage="ALERT_BLOCKED",
                    status="HARD GATE BLOCKED",
                    plan_id=plan_id,
                    payload={
                        "blockers": gate.get("blockers") or [],
                        "strategy_name": strategy_name,
                        "setup_instance_key": instance_key,
                    },
                )

        self.live_page.refresh()
        self.strategy_page.refresh()
        self.live_page.set_status(
            f"Automatic {strategy_name} Trade Plan #{plan_id} saved for {ticker}: {result.decision}. "
            f"This setup instance is complete; a materially new instance can be analyzed later."
        )

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
                "Regular market session is closed. Strategy recognition, automatic Trade Plans and trade alerts are paused; scanner capture/history remain available."
            )
            return

        try:
            attention = self.repo.attention_list(30, limit=100)
        except Exception as exc:
            self.live_page.set_status(f"Strategy pipeline could not read attention list: {type(exc).__name__}: {exc}")
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
            last_scan = self._strategy_scan_last.get(session_id, 0.0)
            if now - last_scan < STRATEGY_SCAN_COOLDOWN_SECONDS:
                continue
            self._strategy_scan_last[session_id] = now
            qualification = self.live_qualification.qualify_session(session_id)
            if self._start_auto_plan(
                ticker=item.ticker,
                session_id=session_id,
                qualification=qualification,
            ):
                break

        self.live_page.refresh()


base.MainWindow = StrategyPipelineMainWindow


def main() -> int:
    return live.stats_ui.main()


if __name__ == "__main__":
    raise SystemExit(main())
