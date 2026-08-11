from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import QTableWidgetItem

from . import desktop_ui_calibration_validator as validator_ui
from . import desktop_ui_market as market_ui
from . import desktop_ui_stats as calibration_stats_ui
from . import desktop_ui_strategy_pipeline as strategy_base
from . import desktop_ui_strategy_pipeline_v3 as current
from . import desktop_ui_trade_stats as trade_stats_ui
from .automatic_outcomes import AutomaticOutcomeStore
from .calibration_stats import CalibrationStatsEngine
from .performance_outcomes import (
    configure_sqlite_for_desktop,
    install_outcome_performance_patches,
)
from .performance_v2 import install_performance_patches
from .qualification import CandidateQualificationEngine


install_performance_patches()
install_outcome_performance_patches()
base = current.base
CALIBRATION_BACKGROUND_INTERVAL_SECONDS = 60.0


_original_feature_snapshot = CalibrationStatsEngine.ensure_feature_snapshot


def _cached_feature_snapshot(self: Any, session_id: int) -> dict[str, Any] | None:
    """Feature snapshots are immutable, so keep decoded copies in memory per engine."""
    cache = getattr(self, "_perf_feature_cache", None)
    if not isinstance(cache, dict):
        cache = {}
        self._perf_feature_cache = cache
    key = int(session_id)
    if key in cache:
        return cache[key]
    value = _original_feature_snapshot(self, key)
    if value is not None:
        cache[key] = value
    return value


CalibrationStatsEngine.ensure_feature_snapshot = _cached_feature_snapshot


def _keep_stats_cache_fresh(engine: Any) -> None:
    cache = getattr(engine, "_perf_stats_cache", None)
    if isinstance(cache, dict):
        # Background refresh owns invalidation/replacement in V4. Prevent the
        # generic short cache TTL from causing a surprise rebuild on the GUI thread.
        cache["built_at"] = time.monotonic()


def _event_revision(store: Any, stage: str | None = None) -> tuple[int, int]:
    try:
        query = "SELECT COUNT(*), COALESCE(MAX(id), 0) FROM live_pipeline_events"
        params: tuple[Any, ...] = ()
        if stage:
            query += " WHERE stage=?"
            params = (stage,)
        with store._connect() as connection:
            row = connection.execute(query, params).fetchone()
        return (int(row[0] or 0), int(row[1] or 0))
    except Exception:
        return (-1, -1)


def _skip_gui_outcome_refresh(self: Any) -> dict[str, int]:
    """Automatic outcomes are refreshed by CalibrationRefreshWorker in V4."""
    return {"session_changes": 0, "review_changes": 0}


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


_original_live_pipeline_refresh = strategy_base.StrategyLiveTradePipelinePage.refresh


def _fast_live_pipeline_refresh(self: Any) -> None:
    revision = _event_revision(self.store)
    if getattr(self, "_perf_event_revision", None) == revision:
        return
    self.table.setUpdatesEnabled(False)
    try:
        _original_live_pipeline_refresh(self)
    finally:
        self.table.setUpdatesEnabled(True)
    self._perf_event_revision = revision


_original_strategy_library_refresh = strategy_base.StrategyLibraryPage.refresh


def _fast_strategy_library_refresh(self: Any) -> None:
    revision = _event_revision(self.store, "STRATEGY_MATCH")
    if getattr(self, "_perf_strategy_revision", None) == revision:
        return
    self.recent.setUpdatesEnabled(False)
    try:
        _original_strategy_library_refresh(self)
    finally:
        self.recent.setUpdatesEnabled(True)
    self._perf_strategy_revision = revision


_original_trade_stats_refresh = trade_stats_ui.TradePlanStatisticsPage.refresh


def _light_trade_stats_refresh(self: Any) -> None:
    """Render the last background-built stats snapshot without re-evaluating plans."""
    _keep_stats_cache_fresh(self.engine)
    cache = getattr(self.engine, "_perf_stats_cache", None)
    built_at = float(cache.get("built_at") or 0.0) if isinstance(cache, dict) else 0.0
    key = (built_at, int(self.minimum_combo.currentData() or 0))
    if getattr(self, "_perf_render_key", None) == key:
        return
    self._last_engine_refresh = time.monotonic()
    _original_trade_stats_refresh(self)
    self._perf_render_key = key


def _light_trade_stats_force_refresh(self: Any) -> None:
    self._perf_render_key = None
    self.refresh()


_original_validator_refresh = validator_ui.CalibrationValidatorPage.refresh


def _light_validator_refresh(self: Any) -> None:
    """Keep candidate display live while using background-built calibration stats."""
    _keep_stats_cache_fresh(self.engine.trade_stats)
    self._last_engine_refresh = time.monotonic()
    _original_validator_refresh(self)


_original_calibration_stats_refresh = calibration_stats_ui.CalibrationStatisticsPage.refresh


def _light_calibration_stats_refresh(self: Any) -> None:
    """Refresh observational statistics at a human-visible cadence without reclassifying outcomes."""
    now = time.monotonic()
    filter_key = (
        int(self.horizon_combo.currentData() or 15),
        int(self.minimum_combo.currentData() or 1),
    )
    if (
        getattr(self, "_perf_calibration_filter", None) == filter_key
        and now - float(getattr(self, "_perf_calibration_rendered_at", 0.0) or 0.0) < 15.0
    ):
        return
    self._last_engine_refresh = now
    _original_calibration_stats_refresh(self)
    self._perf_calibration_filter = filter_key
    self._perf_calibration_rendered_at = now


def _light_calibration_force_refresh(self: Any) -> None:
    self._perf_calibration_rendered_at = 0.0
    self.refresh()


_original_market_page_refresh = market_ui.MarketTrackingPage.refresh


def _throttled_market_page_refresh(self: Any) -> None:
    now = time.monotonic()
    if now - float(getattr(self, "_perf_market_rendered_at", 0.0) or 0.0) < 12.0:
        return
    self._perf_market_rendered_at = now
    _original_market_page_refresh(self)


# These widgets/controllers are constructed during inherited initialization, so
# patch them before V4 calls super().__init__().
current.TradeAlertsPage.refresh = _fast_trade_alerts_refresh
strategy_base.StrategyLiveTradePipelinePage.refresh = _fast_live_pipeline_refresh
strategy_base.StrategyLibraryPage.refresh = _fast_strategy_library_refresh
trade_stats_ui.TradePlanStatisticsPage.refresh = _light_trade_stats_refresh
trade_stats_ui.TradePlanStatisticsPage._force_refresh = _light_trade_stats_force_refresh
validator_ui.CalibrationValidatorPage.refresh = _light_validator_refresh
calibration_stats_ui.CalibrationStatisticsPage.refresh = _light_calibration_stats_refresh
calibration_stats_ui.CalibrationStatisticsPage._force_refresh = _light_calibration_force_refresh
market_ui.MarketTrackingPage.refresh = _throttled_market_page_refresh
market_ui.MarketTrackerController._refresh_automatic_outcomes = _skip_gui_outcome_refresh


class CalibrationRefreshWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, database_path: str | Path, parent: Any = None) -> None:
        super().__init__(parent)
        self.database_path = str(database_path)

    def run(self) -> None:
        try:
            outcomes = AutomaticOutcomeStore(self.database_path).refresh_all_due_outcomes(limit=200)
            engine = CandidateQualificationEngine(self.database_path)
            refresh_result = engine.refresh(limit=500)
            # Build the expensive aggregate cache here too, so the GUI thread can
            # consume a ready snapshot instead of parsing hundreds of plans.
            overview = engine.trade_stats.overview()
            patterns = engine.trade_stats.pattern_stats(min_resolved=0)
            self.completed.emit(
                {
                    "outcomes": outcomes,
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

        configure_sqlite_for_desktop(self.config.database_path)

        # The market tracker updates more slowly than the old 2-second UI loop.
        # Five seconds keeps the display live without rebuilding large tables constantly.
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
        # original strategy pipeline consumes the last completed background snapshot.
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
