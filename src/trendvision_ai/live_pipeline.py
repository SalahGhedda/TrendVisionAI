from __future__ import annotations

import json
import sqlite3
from datetime import datetime, time as clock_time, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .trade_plan_calibration_v3 import MAX_ACTIONABLE_OBSERVED_SPREAD_PCT


PIPELINE_VERSION = 2
NEW_YORK = ZoneInfo("America/New_York")


def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def _now_iso() -> str:
    return _now().isoformat(timespec="seconds")


def regular_session_state(now: datetime | None = None) -> dict[str, Any]:
    """Simple regular-session gate; stale market data still blocks holidays/closures."""
    current = now or _now()
    eastern = current.astimezone(NEW_YORK)
    weekday = eastern.weekday() < 5
    local_time = eastern.time().replace(tzinfo=None)
    open_now = bool(
        weekday
        and clock_time(9, 30) <= local_time < clock_time(16, 0)
    )
    if open_now:
        reason = "Regular US equity session is within 09:30-16:00 America/New_York."
    elif not weekday:
        reason = "Regular US equity session is closed on weekends."
    else:
        reason = "Outside the configured 09:30-16:00 America/New_York regular session."
    return {
        "open": open_now,
        "checked_at": current.isoformat(timespec="seconds"),
        "eastern_time": eastern.isoformat(timespec="seconds"),
        "reason": reason,
    }


class LivePipelineStore:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=3.0)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS live_pipeline_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    pipeline_version INTEGER NOT NULL,
                    session_id INTEGER,
                    ticker TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    status TEXT NOT NULL,
                    plan_id INTEGER,
                    dedup_key TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_live_pipeline_created
                    ON live_pipeline_events(created_at DESC, id DESC);
                CREATE INDEX IF NOT EXISTS idx_live_pipeline_session
                    ON live_pipeline_events(session_id, stage, created_at DESC);
                """
            )

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        try:
            payload = json.loads(item.get("payload_json") or "{}")
        except json.JSONDecodeError:
            payload = {}
        item["payload"] = payload if isinstance(payload, dict) else {}
        return item

    def record_once(
        self,
        *,
        dedup_key: str,
        ticker: str,
        stage: str,
        status: str,
        session_id: int | None = None,
        plan_id: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> tuple[bool, dict[str, Any] | None]:
        with self._connect() as connection:
            try:
                cursor = connection.execute(
                    """
                    INSERT INTO live_pipeline_events (
                        created_at, pipeline_version, session_id, ticker, stage,
                        status, plan_id, dedup_key, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        _now_iso(),
                        PIPELINE_VERSION,
                        int(session_id) if session_id is not None else None,
                        ticker.upper().strip(),
                        stage,
                        status,
                        int(plan_id) if plan_id is not None else None,
                        dedup_key,
                        json.dumps(payload or {}, ensure_ascii=False, sort_keys=True),
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM live_pipeline_events WHERE id=?",
                    (cursor.lastrowid,),
                ).fetchone()
                return True, self._decode(row) if row is not None else None
            except sqlite3.IntegrityError:
                row = connection.execute(
                    "SELECT * FROM live_pipeline_events WHERE dedup_key=?",
                    (dedup_key,),
                ).fetchone()
                return False, self._decode(row) if row is not None else None

    def latest_for_session(self, session_id: int, stage: str | None = None) -> dict[str, Any] | None:
        query = "SELECT * FROM live_pipeline_events WHERE session_id=?"
        params: list[Any] = [int(session_id)]
        if stage:
            query += " AND stage=?"
            params.append(stage)
        query += " ORDER BY created_at DESC, id DESC LIMIT 1"
        with self._connect() as connection:
            row = connection.execute(query, params).fetchone()
        return self._decode(row) if row is not None else None

    def list_events(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM live_pipeline_events
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [self._decode(row) for row in rows]

    def existing_trade_plan_for_session(
        self,
        session_id: int,
        *,
        strategy_id: str | None = None,
        limit: int = 500,
    ) -> dict[str, Any] | None:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT id, ticker, created_at, decision, snapshot_json, result_json
                    FROM trade_plans
                    ORDER BY created_at DESC, id DESC
                    LIMIT ?
                    """,
                    (max(1, int(limit)),),
                ).fetchall()
        except sqlite3.OperationalError:
            return None
        requested_strategy = str(strategy_id or "").strip()
        for row in rows:
            item = dict(row)
            try:
                snapshot = json.loads(item.get("snapshot_json") or "{}")
            except json.JSONDecodeError:
                continue
            market = snapshot.get("alpaca_market_context") or {}
            try:
                stored_session = int(market.get("session_id"))
            except (TypeError, ValueError):
                continue
            if stored_session != int(session_id):
                continue
            if requested_strategy:
                primary = ((snapshot.get("strategy_context") or {}).get("primary") or {})
                if str(primary.get("strategy_id") or "").strip() != requested_strategy:
                    continue
            try:
                result = json.loads(item.get("result_json") or "{}")
            except json.JSONDecodeError:
                result = {}
            item["snapshot"] = snapshot if isinstance(snapshot, dict) else {}
            item["result"] = result if isinstance(result, dict) else {}
            return item
        return None


def final_trade_alert_gate(
    *,
    qualification: dict[str, Any],
    plan: dict[str, Any],
    snapshot: dict[str, Any],
    strategy_validation: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Deterministic blockers that AI cannot override before a user-facing alert.

    A recognized known setup is now the primary strategy requirement. Historical
    calibration is a validator/filter: mature negative evidence vetoes an alert,
    while immature history does not force the system to rediscover the setup from
    scratch before it can be evaluated.
    """
    blockers: list[str] = []
    session = regular_session_state(now)
    if not session["open"]:
        blockers.append("MARKET_CLOSED")

    strategy = snapshot.get("strategy_context") or {}
    primary = strategy.get("primary") or {}
    if not strategy.get("recognized") or str(primary.get("status") or "") != "CANDIDATE":
        blockers.append("NO_RECOGNIZED_STRATEGY")

    validation = strategy_validation or snapshot.get("strategy_validation") or {}
    if str(validation.get("status") or "").upper() == "MATURE NEGATIVE":
        blockers.append("STRATEGY_CALIBRATION_NEGATIVE")

    if str(qualification.get("status") or "").upper() == "MONITOR / RISK":
        blockers.append("DETECTION_CALIBRATION_RISK")

    if str(plan.get("decision") or "") != "POTENTIAL TRADE":
        blockers.append("TRADE_PLAN_NOT_POTENTIAL")

    market = snapshot.get("alpaca_market_context") or {}
    freshness = market.get("market_event_freshness") or {}
    latest = market.get("latest") or {}
    if not market.get("current_context_usable"):
        blockers.append("STALE_MARKET_CONTEXT")
    if not freshness.get("latest_quote_fresh"):
        blockers.append("NO_FRESH_QUOTE")

    spread_pct = latest.get("spread_pct")
    try:
        spread_value = float(spread_pct) if spread_pct is not None else None
    except (TypeError, ValueError):
        spread_value = None
    if spread_value is None:
        blockers.append("UNKNOWN_SPREAD")
    elif spread_value >= MAX_ACTIONABLE_OBSERVED_SPREAD_PCT:
        blockers.append("SPREAD_TOO_WIDE")

    required = ("entry_low", "entry_high", "stop_loss", "target_1", "target_2")
    try:
        values = {key: float(plan.get(key)) for key in required}
    except (TypeError, ValueError):
        values = {}
    if len(values) != len(required):
        blockers.append("MISSING_LEVELS")
    else:
        coherent = (
            values["entry_low"] <= values["entry_high"]
            and values["stop_loss"] < values["entry_low"]
            and values["target_1"] > values["entry_high"]
            and values["target_2"] >= values["target_1"]
        )
        if not coherent:
            blockers.append("INCOHERENT_LEVELS")

        reference = None
        max_extension = None
        try:
            reference = float((primary.get("key_levels") or {}).get("entry_reference"))
            max_extension = float((primary.get("plan_constraints") or {}).get("max_entry_extension_pct"))
        except (TypeError, ValueError):
            reference = None
            max_extension = None
        if reference is not None and reference > 0 and max_extension is not None and len(values) == len(required):
            entry_extension = (values["entry_high"] / reference - 1.0) * 100.0
            if entry_extension > max_extension:
                blockers.append("STRATEGY_ENTRY_TOO_EXTENDED")

    if str(plan.get("risk_level") or "").upper() == "EXTREME":
        blockers.append("EXTREME_RISK")
    if str(plan.get("chart_structure") or "").upper() in {"DANGEROUS", "UNCLEAR"}:
        blockers.append("UNSAFE_CHART_STRUCTURE")

    trendvision = snapshot.get("trendvision") or {}
    events = ((trendvision.get("recent_convergence") or {}).get("events") or [])
    halt_count = sum(
        1
        for event in events
        if str(event.get("channel") or "") == "halt-scanner"
        or bool((event.get("data") or {}).get("halt_status"))
    )
    if halt_count >= 2:
        blockers.append("MULTIPLE_RECENT_HALTS")

    return {
        "allowed": not blockers,
        "blockers": list(dict.fromkeys(blockers)),
        "market_session": session,
        "observed_spread_pct": spread_value,
        "spread_guardrail_pct": MAX_ACTIONABLE_OBSERVED_SPREAD_PCT,
        "strategy_id": primary.get("strategy_id"),
        "strategy_name": primary.get("name"),
        "strategy_validation_status": validation.get("status"),
        "detection_calibration_status": qualification.get("status"),
    }


def qualification_summary(qualification: dict[str, Any]) -> str:
    positives = qualification.get("positive_patterns") or []
    names = [str(item.get("pattern") or "") for item in positives[:3] if item.get("pattern")]
    if names:
        return "; ".join(names)
    return str(qualification.get("reason") or "Calibration evidence is not mature enough for a directional vote.")


def strategy_summary(snapshot: dict[str, Any]) -> str:
    primary = ((snapshot.get("strategy_context") or {}).get("primary") or {})
    if not primary:
        return "No recognized strategy."
    return f"{primary.get('name') or primary.get('strategy_id')} (score {primary.get('score') or '-'} / 100)"
