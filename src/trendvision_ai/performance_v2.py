from __future__ import annotations

import json
import sqlite3
import time
from datetime import timedelta
from typing import Any

from . import calibration_stats as calibration_mod
from . import trade_alert_journal as journal_mod
from . import trade_plan_stats as stats_mod
from . import trade_plans as trade_plans_mod


_STATS_CACHE_SECONDS = 20.0
_installed = False


def _fast_events_before_trigger(
    self: Any,
    *,
    ticker: str,
    trigger_at: str,
    window_minutes: int,
) -> list[dict[str, Any]]:
    """Read only the detection-time scanner window instead of all ticker history."""
    trigger_time = calibration_mod._parse_time(trigger_at)
    if trigger_time is None:
        return []
    start_time = trigger_time - timedelta(minutes=max(1, int(window_minutes)))
    try:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, received_at, channel, event_type, headline, data_json
                FROM scanner_events
                WHERE UPPER(ticker)=?
                  AND julianday(received_at) >= julianday(?)
                  AND julianday(received_at) <= julianday(?)
                ORDER BY received_at ASC, id ASC
                """,
                (
                    ticker.upper().strip(),
                    start_time.isoformat(timespec="seconds"),
                    trigger_time.isoformat(timespec="seconds"),
                ),
            ).fetchall()
    except sqlite3.OperationalError:
        return []

    events: list[dict[str, Any]] = []
    for row in rows:
        data = calibration_mod._decode_json(row["data_json"], {})
        events.append(
            {
                "id": int(row["id"]),
                "received_at": row["received_at"],
                "channel": str(row["channel"] or ""),
                "event_type": str(row["event_type"] or ""),
                "headline": str(row["headline"] or ""),
                "data": data if isinstance(data, dict) else {},
            }
        )
    return events


def _fast_ensure_feature_snapshots(self: Any, limit: int = 500) -> int:
    """Ask SQLite for only missing feature snapshots instead of probing every session."""
    try:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT s.id
                FROM market_tracking_sessions s
                LEFT JOIN calibration_feature_snapshots c ON c.session_id=s.id
                WHERE c.session_id IS NULL
                ORDER BY s.started_at DESC, s.id DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
    except sqlite3.OperationalError:
        return 0

    created = 0
    for row in rows:
        if self.ensure_feature_snapshot(int(row["id"])) is not None:
            created += 1
    return created


def _fast_evaluate(
    self: Any,
    plan_id: int,
    *,
    horizon_minutes: int = trade_plans_mod.PLAN_HORIZON_MINUTES,
) -> dict[str, Any] | None:
    """Evaluate one plan using only its session/time-window samples.

    The old implementation loaded every stored sample for the ticker and then
    discarded rows outside the plan horizon in Python. This version uses the
    existing SQLite indexes to bound the query first.
    """
    plan = self._plan(plan_id)
    if plan is None:
        return None
    created = trade_plans_mod._parse_time(plan.get("created_at"))
    if created is None:
        return None

    horizon_minutes = max(1, int(horizon_minutes))
    deadline = created + timedelta(minutes=horizon_minutes)
    complete = trade_plans_mod._seconds_between(trade_plans_mod._now(), deadline) >= 0

    entry_low = trade_plans_mod._num(plan.get("entry_low"))
    entry_high = trade_plans_mod._num(plan.get("entry_high"))
    stop = trade_plans_mod._num(plan.get("stop_loss"))
    target_1 = trade_plans_mod._num(plan.get("target_1"))
    target_2 = trade_plans_mod._num(plan.get("target_2"))

    actionable = (
        str(plan.get("decision") or "") == "POTENTIAL TRADE"
        and all(value is not None for value in (entry_low, entry_high, stop, target_1, target_2))
    )
    if not actionable:
        return self._save_evaluation(
            plan_id,
            horizon_minutes,
            {
                "status": "NO ACTIONABLE LEVELS",
                "reason": "The saved review did not produce a POTENTIAL TRADE plan.",
                "horizon_complete": complete,
                "sample_count": 0,
            },
        )

    snapshot = plan.get("snapshot") or {}
    market = snapshot.get("alpaca_market_context") or {}
    session_id = market.get("session_id")

    start_iso = created.isoformat(timespec="seconds")
    end_iso = deadline.isoformat(timespec="seconds")
    try:
        with self._connect() as connection:
            if session_id is not None:
                rows = connection.execute(
                    """
                    SELECT captured_at, trade_price, minute_close
                    FROM market_samples
                    WHERE session_id=?
                      AND julianday(captured_at) >= julianday(?)
                      AND julianday(captured_at) <= julianday(?)
                    ORDER BY captured_at ASC, id ASC
                    """,
                    (int(session_id), start_iso, end_iso),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT captured_at, trade_price, minute_close
                    FROM market_samples
                    WHERE UPPER(ticker)=?
                      AND julianday(captured_at) >= julianday(?)
                      AND julianday(captured_at) <= julianday(?)
                    ORDER BY captured_at ASC, id ASC
                    """,
                    (str(plan.get("ticker") or "").upper(), start_iso, end_iso),
                ).fetchall()
    except (sqlite3.OperationalError, TypeError, ValueError):
        rows = []

    samples: list[tuple[Any, float]] = []
    for row in rows:
        captured = trade_plans_mod._parse_time(row["captured_at"])
        price = trade_plans_mod._sample_price(row)
        if captured is None or price is None or price <= 0:
            continue
        samples.append((captured, price))

    if not samples:
        return self._save_evaluation(
            plan_id,
            horizon_minutes,
            {
                "status": "INSUFFICIENT DATA" if complete else "WAITING FOR MARKET DATA",
                "reason": "No usable Alpaca samples exist after the plan timestamp.",
                "horizon_complete": complete,
                "sample_count": 0,
            },
        )

    entry_index = next(
        (i for i, (_captured, price) in enumerate(samples) if entry_low <= price <= entry_high),
        None,
    )
    if entry_index is None:
        return self._save_evaluation(
            plan_id,
            horizon_minutes,
            {
                "status": "ENTRY NOT REACHED" if complete else "WAITING FOR ENTRY",
                "reason": "Sampled trade price has not entered the proposed entry zone.",
                "horizon_complete": complete,
                "sample_count": len(samples),
                "final_price": samples[-1][1],
            },
        )

    entry_time, entry_price = samples[entry_index]
    t1_time = None
    t2_time = None
    stop_time = None
    prices: list[float] = []
    for captured, price in samples[entry_index:]:
        prices.append(price)
        if price <= stop:
            stop_time = captured
            break
        if price >= target_2:
            t1_time = t1_time or captured
            t2_time = captured
            break
        if price >= target_1 and t1_time is None:
            t1_time = captured

    if t2_time is not None:
        status = "TARGET 2 HIT"
    elif stop_time is not None and t1_time is not None:
        status = "TARGET 1 THEN STOP"
    elif stop_time is not None:
        status = "STOP HIT FIRST"
    elif t1_time is not None and complete:
        status = "TARGET 1 ONLY"
    elif t1_time is not None:
        status = "TARGET 1 HIT / OPEN"
    elif complete:
        status = "NO TARGET / NO STOP"
    else:
        status = "OPEN / IN PROGRESS"

    return self._save_evaluation(
        plan_id,
        horizon_minutes,
        {
            "status": status,
            "reason": "Objective sampled-price evaluation of the saved plan.",
            "horizon_complete": complete,
            "sample_count": len(samples),
            "entry_reached_at": entry_time.isoformat(timespec="seconds"),
            "entry_price": entry_price,
            "target_1_hit_at": t1_time.isoformat(timespec="seconds") if t1_time else None,
            "target_2_hit_at": t2_time.isoformat(timespec="seconds") if t2_time else None,
            "stop_hit_at": stop_time.isoformat(timespec="seconds") if stop_time else None,
            "final_price": prices[-1] if prices else entry_price,
            "max_return_pct": trade_plans_mod._pct_change(max(prices), entry_price) if prices else 0.0,
            "max_drawdown_pct": trade_plans_mod._pct_change(min(prices), entry_price) if prices else 0.0,
        },
    )


def _fast_refresh_evaluations(self: Any, limit: int = 200) -> int:
    """Refresh only actionable plans whose evaluation can still change."""
    terminal_while_open = (
        "TARGET 2 HIT",
        "STOP HIT FIRST",
        "TARGET 1 THEN STOP",
        "NO ACTIONABLE LEVELS",
    )
    try:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT p.id, COALESCE(e.status, '') AS evaluation_status
                FROM trade_plans p
                LEFT JOIN trade_plan_evaluations e ON e.plan_id=p.id
                WHERE p.decision='POTENTIAL TRADE'
                  AND (
                    e.plan_id IS NULL
                    OR (
                        COALESCE(e.horizon_complete, 0)=0
                        AND COALESCE(e.status, '') NOT IN (?, ?, ?, ?)
                    )
                  )
                ORDER BY p.created_at DESC, p.id DESC
                LIMIT ?
                """,
                (*terminal_while_open, max(1, int(limit))),
            ).fetchall()
    except sqlite3.OperationalError:
        return 0

    changed = 0
    for row in rows:
        before = str(row["evaluation_status"] or "")
        after = str((self.evaluate(int(row["id"])) or {}).get("status") or "")
        if before != after:
            changed += 1
    return changed


def _feature_tag_map(self: Any) -> dict[int, list[str]]:
    try:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT session_id, tags_json FROM calibration_feature_snapshots"
            ).fetchall()
    except sqlite3.OperationalError:
        return {}

    result: dict[int, list[str]] = {}
    for row in rows:
        tags = stats_mod._decode_json(row["tags_json"], [])
        result[int(row["session_id"])] = [str(tag) for tag in tags] if isinstance(tags, list) else []
    return result


def _compute_stats_cache(self: Any) -> dict[str, Any]:
    rows = self._rows()
    potential = [row for row in rows if self._is_actionable(row)]
    overview = self._summarize(potential, "ALL POTENTIAL TRADE")
    overview.update(
        {
            "total_plans": len(rows),
            "potential_plans": len(potential),
            "watch_plans": sum(1 for row in rows if row.get("decision") == "WATCH"),
            "rejected_plans": sum(1 for row in rows if row.get("decision") == "REJECT"),
        }
    )

    feature_tags = _feature_tag_map(self)
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in potential:
        snapshot = row.get("snapshot") or {}
        market = snapshot.get("alpaca_market_context") or {}
        try:
            session_id = int(market.get("session_id"))
        except (TypeError, ValueError):
            session_id = -1
        tags = list(feature_tags.get(session_id, []))
        tags.extend(self._plan_tags(row))
        for tag in dict.fromkeys(tags):
            groups.setdefault(str(tag), []).append(row)

    patterns = [self._summarize(group_rows, pattern) for pattern, group_rows in groups.items()]
    patterns.sort(
        key=lambda row: (
            int(row.get("resolved_count") or 0),
            int(row.get("entered_count") or 0),
            int(row.get("actionable_count") or 0),
            str(row.get("pattern") or ""),
        ),
        reverse=True,
    )
    cache = {
        "built_at": time.monotonic(),
        "overview": overview,
        "patterns": patterns,
    }
    self._perf_stats_cache = cache
    return cache


def _stats_cache(self: Any) -> dict[str, Any]:
    cache = getattr(self, "_perf_stats_cache", None)
    if not isinstance(cache, dict):
        return _compute_stats_cache(self)
    age = time.monotonic() - float(cache.get("built_at") or 0.0)
    if age > _STATS_CACHE_SECONDS:
        return _compute_stats_cache(self)
    return cache


def _fast_stats_overview(self: Any) -> dict[str, Any]:
    return dict(_stats_cache(self)["overview"])


def _fast_pattern_stats(self: Any, *, min_resolved: int = 1) -> list[dict[str, Any]]:
    minimum = max(0, int(min_resolved))
    return [
        dict(row)
        for row in _stats_cache(self)["patterns"]
        if int(row.get("resolved_count") or 0) >= minimum
    ]


def _fast_pattern_map(self: Any, *, min_resolved: int = 1) -> dict[str, dict[str, Any]]:
    return {
        str(row["pattern"]): row
        for row in self.pattern_stats(min_resolved=min_resolved)
    }


def _optimized_journal_sync(
    self: Any,
    live_store: Any,
    *,
    limit: int = 1000,
) -> int:
    """Sync only FINAL_TRADE_ALERT rows newer than the journal's last event id."""
    if getattr(live_store, "database_path", None) != self.database_path:
        return _original_journal_sync(self, live_store, limit=limit)

    try:
        with self._connect() as connection:
            last_id = int(
                connection.execute(
                    "SELECT COALESCE(MAX(live_event_id), 0) FROM trade_alert_journal"
                ).fetchone()[0]
                or 0
            )
            rows = connection.execute(
                """
                SELECT id, created_at, ticker, session_id, plan_id, payload_json
                FROM live_pipeline_events
                WHERE stage='FINAL_TRADE_ALERT' AND id>?
                ORDER BY id ASC
                LIMIT ?
                """,
                (last_id, max(1, int(limit))),
            ).fetchall()

            changes = 0
            for event in rows:
                try:
                    payload = json.loads(event["payload_json"] or "{}")
                except json.JSONDecodeError:
                    payload = {}
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO trade_alert_journal (
                        live_event_id, created_at, ticker, session_id, plan_id,
                        strategy_id, strategy_name, entry_low, entry_high,
                        stop_loss, target_1, target_2,
                        risk_reward_target_1, risk_reward_target_2
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(event["id"]),
                        str(event["created_at"] or journal_mod._now_iso()),
                        str(event["ticker"] or "?").upper(),
                        int(event["session_id"]) if event["session_id"] is not None else None,
                        int(event["plan_id"]) if event["plan_id"] is not None else None,
                        str(payload.get("strategy_id") or "") or None,
                        str(payload.get("strategy_name") or "") or None,
                        journal_mod._num(payload.get("entry_low")),
                        journal_mod._num(payload.get("entry_high")),
                        journal_mod._num(payload.get("stop_loss")),
                        journal_mod._num(payload.get("target_1")),
                        journal_mod._num(payload.get("target_2")),
                        journal_mod._num(payload.get("risk_reward_target_1")),
                        journal_mod._num(payload.get("risk_reward_target_2")),
                    ),
                )
                changes += int(cursor.rowcount or 0)
        return changes
    except sqlite3.OperationalError:
        return _original_journal_sync(self, live_store, limit=limit)


def _journal_revision(self: Any) -> tuple[int, int, int]:
    try:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*), COALESCE(MAX(id), 0) FROM trade_alert_journal"
            ).fetchone()
        return (
            int(row[0] or 0),
            int(row[1] or 0),
            int(getattr(self, "_perf_manual_revision", 0) or 0),
        )
    except sqlite3.OperationalError:
        return (0, 0, int(getattr(self, "_perf_manual_revision", 0) or 0))


def _tracked_set_manual_result(self: Any, alert_id: int, result: str) -> dict[str, Any] | None:
    value = _original_set_manual_result(self, alert_id, result)
    self._perf_manual_revision = int(getattr(self, "_perf_manual_revision", 0) or 0) + 1
    return value


def _invalidate_stats_after_refresh(self: Any, limit: int = 500) -> dict[str, int]:
    result = _original_stats_refresh(self, limit=limit)
    self._perf_stats_cache = None
    return result


_original_stats_refresh = stats_mod.TradePlanStatsEngine.refresh
_original_journal_sync = journal_mod.TradeAlertJournalStore.sync_from_live_events
_original_set_manual_result = journal_mod.TradeAlertJournalStore.set_manual_result


def install_performance_patches() -> None:
    """Install idempotent performance patches used by the desktop application."""
    global _installed
    if _installed:
        return

    calibration_mod.CalibrationStatsEngine._events_before_trigger = _fast_events_before_trigger
    calibration_mod.CalibrationStatsEngine.ensure_feature_snapshots = _fast_ensure_feature_snapshots

    trade_plans_mod.TradePlanStore.evaluate = _fast_evaluate
    trade_plans_mod.TradePlanStore.refresh_evaluations = _fast_refresh_evaluations

    stats_mod.TradePlanStatsEngine.refresh = _invalidate_stats_after_refresh
    stats_mod.TradePlanStatsEngine.overview = _fast_stats_overview
    stats_mod.TradePlanStatsEngine.pattern_stats = _fast_pattern_stats
    stats_mod.TradePlanStatsEngine.pattern_map = _fast_pattern_map

    journal_mod.TradeAlertJournalStore.sync_from_live_events = _optimized_journal_sync
    journal_mod.TradeAlertJournalStore.revision = _journal_revision
    journal_mod.TradeAlertJournalStore.set_manual_result = _tracked_set_manual_result

    _installed = True
