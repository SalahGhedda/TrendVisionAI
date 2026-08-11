from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import QTableWidgetItem

from . import desktop_ui_calibration_validator as validator_ui
from . import desktop_ui_strategy_pipeline as strategy_base
from . import desktop_ui_strategy_pipeline_v3 as current
from . import desktop_ui_trade_stats as trade_stats_ui
from .performance_v2 import install_performance_patches
from .qualification import CandidateQualificationEngine


install_performance_patches()
base = current.base
CALIBRATION_BACKGROUND_INTERVAL_SECONDS = 60.0


def _keep_stats_cache_fresh(engine: Any) -> None:
    cache = getattr(engine, "_perf_stats_cache", None)
    if isinstance(cache, dict):
        # Background refresh owns invalidation/replacement in V4. Prevent the
        # generic short cache TTL from causing a surprise rebuild on the GUI thread.
        cache["built_at"] = time.monotonic()


def _fast_trade_alerts_refresh(self: Any, select_alert_id: int | None = None) -> None:
    """Redraw the Trades table only when an alert/result actually changed."""
    self.store.sync_from_live_events(self.live_store, limit=1000)
    stats = self.store.stats()
    self.total_card.set_value(stats["total"])
    self.open_card.set_value(stats["open"])
    self.win_card.set_value(stats["wins"])
    self.loss_card.set_value(stats["losses"])
    rate = stats.get("manual_win_rate_pct")
    self.rate_card.set_value(f"{rate:.1f}%" if rate is not None else "-")

    revision = self.store.revision()
    previous = getattr(self, "_perf_render_revision", None)
    if select_alert_id is None and previous == revision:
        return

    alerts = self.store.list_alerts(limit=500)
    self.table.setUpdatesEnabled(False)
    try:
        self.table.setRowCount(len(alerts))
        selected_row = -1
        for row_index, alert in enumerate(alerts):
            entry = f"{current._price(alert.get('entry_low'))}–{current._price(alert.get('entry_high'))}"
            values = [
                current._display_time(alert.get("created_at")),
                alert.get("ticker") or "-",
                alert.get("strategy_name") or alert.get("strategy_id") or "-",
                entry,
                current._price(alert.get("stop_loss")),
                current._price(alert.get("target_1")),
                current._price(alert.get("target_2")),
                current._rr(alert.get("risk_reward_target_1")),
                current._rr(alert.get("risk_reward_target_2")),
                alert.get("manual_result") or "OPEN",
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                if column != 2:
                    cell.setTextAlignment(Qt.AlignCenter)
                if column == 0:
                    cell.setData(Qt.UserRole, int(alert["id"]))
                self.table.setItem(row_index, column, cell)
            if select_alert_id is not None and int(alert["id"]) == int(select_alert_id):
                selected_row = row_index

        if selected_row >= 0:
            self.table.selectRow(selected_row)
        elif alerts and self.table.currentRow() < 0:
            self.table.selectRow(0)
        else:
            self._selection_changed()
    finally:
        self.table.setUpdatesEnabled(True)

    self._perf_render_revision = revision


_original_trade_stats_refresh = trade_stats_ui.TradePlanStatisticsPage.refresh


def _light_trade_stats_refresh(self: Any) -> None:
    """Render the last background-built stats snapshot without re-evaluating plans."""
    _keep_stats_cache_fresh(self.engine)
    cache = getattr(self.engine, "_perf_stats_cache", None)
    built_at = float(cache.get("built_at") or 0.0) if isinstance(cache, dict) else 0.0
    key = (built_at, int(self.minimum_combo.currentData() or 0))
    if getattr(self, "_perf_render_key", None) == key:
        return
    # The inherited page refreshes the engine only when this timestamp is old.
    # Keep it current because the V4 background worker owns that heavy work.
    self._last_engine_refresh = time.monotonic()
    _original_trade_stats_refresh(self)
    self._perf_render_key = key


_original_validator_refresh = validator_ui.CalibrationValidatorPage.refresh


def _light_validator_refresh(self: Any) -> None:
    """Keep candidate display live while using background-built calibration stats."""
    _keep_stats_cache_fresh(self.engine.trade_stats)
    self._last_engine_refresh = time.monotonic()
    _original_validator_refresh(self)


# These widgets are constructed during the inherited window initialization, so
# patch their refresh methods before V4 calls super().__init__().
current.TradeAlertsPage.refresh = _fast_trade_alerts_refresh
trade_stats_ui.TradePlanStatisticsPage.refresh = _light_trade_stats_refresh
validator_ui.CalibrationValidatorPage.refresh = _light_validator_refresh


class CalibrationRefreshWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, database_path: str | Path, parent: Any = None) -> None:
        super().__init__(parent)
        self.database_path = str(database_path)

    def run(self) -> None:
        try:
            engine = CandidateQualificationEngine(self.database_path)
            refresh_result = engine.refresh(limit=500)
            # Build the expensive aggregate cache here too, so the GUI thread can
            # consume a ready snapshot instead of parsing hundreds of plans.
            overview = engine.trade_stats.overview()
            patterns = engine.trade_stats.pattern_stats(min_resolved=0)
            self.completed.emit(
                {
                    "refresh": refresh_result,
                    "overview": overview,
                    "patterns": patterns,
                }
            )
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class StrategyPipelineMainWindowV4(current.StrategyPipelineMainWindowV3):
    """Responsive pipeline shell: heavy calibration stays off the Qt GUI thread."""

    def __init__(self) -> None:
        self._perf_calibration_worker: CalibrationRefreshWorker | None = None
        self._perf_last_calibration_refresh = 0.0
        self._perf_calibration_ready = False
        super().__init__()

        # The underlying market tracker updates at a slower cadence than the old
        # 2-second UI redraw loop. Five seconds keeps the display responsive while
        # avoiding needless reconstruction of large tables.
        self.refresh_timer.setInterval(5000)

    def _start_background_calibration_if_due(self) -> bool:
        now = time.monotonic()
        worker = self._perf_calibration_worker
        if worker is not None and worker.isRunning():
            return True
        if (
            self._perf_calibration_ready
            and now - self._perf_last_calibration_refresh < CALIBRATION_BACKGROUND_INTERVAL_SECONDS
        ):
            return False

        self._perf_last_calibration_refresh = now
        worker = CalibrationRefreshWorker(self.config.database_path, self)
        worker.completed.connect(self._calibration_refresh_completed)
        worker.failed.connect(self._calibration_refresh_failed)
        self._perf_calibration_worker = worker
        worker.start()
        return True

    def _apply_stats_cache(self, engine: Any, overview: dict[str, Any], patterns: list[dict[str, Any]]) -> None:
        engine._perf_stats_cache = {
            "built_at": time.monotonic(),
            "overview": dict(overview),
            "patterns": [dict(row) for row in patterns],
        }

    def _calibration_refresh_completed(self, payload: dict[str, Any]) -> None:
        overview = dict(payload.get("overview") or {})
        patterns = [dict(row) for row in (payload.get("patterns") or [])]

        self._apply_stats_cache(self.live_qualification.trade_stats, overview, patterns)
        try:
            self._apply_stats_cache(self.trade_stats_page.engine, overview, patterns)
            self.trade_stats_page._perf_render_key = None
        except Exception:
            pass
        try:
            self._apply_stats_cache(self.qualification_page.engine.trade_stats, overview, patterns)
        except Exception:
            pass

        self._perf_calibration_ready = True
        QTimer.singleShot(0, self._run_live_pipeline)

    def _calibration_refresh_failed(self, message: str) -> None:
        # Keep the last successfully built cache when a later refresh fails. On
        # startup, retry soon rather than forcing the heavy fallback onto the GUI.
        if not self._perf_calibration_ready:
            self._perf_last_calibration_refresh = time.monotonic() - 45.0
        try:
            self.live_page.set_status(f"Background calibration refresh deferred: {message}")
        except Exception:
            pass

    def _run_live_pipeline(self) -> None:
        self._start_background_calibration_if_due()
        if not self._perf_calibration_ready:
            self.live_page.set_status(
                "Loading calibration/statistics in the background. Scanner capture and market tracking remain active."
            )
            return

        _keep_stats_cache_fresh(self.live_qualification.trade_stats)

        # Skip V2's synchronous self.live_qualification.refresh() override. The
        # original strategy pipeline logic is lightweight enough after the DB and
        # stats-cache optimizations and can consume the last completed snapshot.
        strategy_base.StrategyPipelineMainWindow._run_live_pipeline(self)

    def closeEvent(self, event: Any) -> None:
        worker = self._perf_calibration_worker
        if worker is not None and worker.isRunning():
            worker.wait(10000)
        super().closeEvent(event)


base.MainWindow = StrategyPipelineMainWindowV4


def main() -> int:
    return current.main()


if __name__ == "__main__":
    raise SystemExit(main())
