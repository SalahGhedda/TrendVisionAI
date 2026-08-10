from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .market_data import MarketDataStore, TRACKING_HORIZONS


SCOPE_MARKET_SESSION = "MARKET_SESSION"
SCOPE_AI_REVIEW = "AI_REVIEW"

AUTO_OUTCOME_LABELS = (
    "STRONG UP CONTINUATION",
    "MODEST UP CONTINUATION",
    "SPIKE THEN REVERSAL",
    "TWO-SIDED VOLATILITY",
    "STRONG DOWN MOVE",
    "NEGATIVE OUTCOME",
    "MIXED / RANGE",
    "INSUFFICIENT DATA",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _parse_time(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _seconds_between(later: datetime, earlier: datetime) -> float:
    try:
        return (later - earlier).total_seconds()
    except TypeError:
        return (later.replace(tzinfo=None) - earlier.replace(tzinfo=None)).total_seconds()


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def classify_market_path(
    metrics: dict[str, Any],
    *,
    halt_count: int = 0,
) -> dict[str, Any]:
    """Classify an observed price path using only objective stored measurements.

    These labels describe what happened after the reference timestamp. They are
    not entry/exit instructions and deliberately avoid subjective concepts such
    as whether a setup was "tradeable".
    """
    target = max(1, int(metrics.get("target_minutes") or 0))
    sample_count = int(metrics.get("sample_count") or 0)
    coverage_pct = float(metrics.get("coverage_pct") or 0.0)
    horizon_complete = bool(metrics.get("horizon_complete"))
    fresh_to_horizon = bool(metrics.get("fresh_to_horizon"))
    return_pct = _num(metrics.get("return_pct"))
    mfe_pct = _num(metrics.get("mfe_pct"))
    mae_pct = _num(metrics.get("mae_pct"))
    max_spread_pct = _num(metrics.get("max_spread_pct"))

    flags: list[str] = []
    if int(halt_count or 0) > 0:
        flags.append(f"HALTS_OBSERVED:{int(halt_count)}")
    if max_spread_pct is not None and max_spread_pct >= 3.0:
        flags.append("WIDE_SPREAD_OBSERVED")
    if mfe_pct is not None and mfe_pct >= 30.0:
        flags.append("EXTREME_UPSIDE_EXCURSION")
    if mae_pct is not None and mae_pct <= -20.0:
        flags.append("EXTREME_DOWNSIDE_EXCURSION")

    enough_data = (
        bool(metrics.get("available"))
        and horizon_complete
        and fresh_to_horizon
        and coverage_pct >= 90.0
        and sample_count >= 3
        and return_pct is not None
        and mfe_pct is not None
        and mae_pct is not None
    )
    if not enough_data:
        missing: list[str] = []
        if not metrics.get("available"):
            missing.append("no market samples")
        if not horizon_complete:
            missing.append("horizon still in progress")
        if horizon_complete and not fresh_to_horizon:
            missing.append("no fresh sample near horizon end")
        if coverage_pct < 90.0:
            missing.append(f"coverage {coverage_pct:.1f}%")
        if sample_count < 3:
            missing.append(f"only {sample_count} sample(s)")
        if return_pct is None or mfe_pct is None or mae_pct is None:
            missing.append("missing return/MFE/MAE")
        return {
            "label": "INSUFFICIENT DATA",
            "confidence": "LOW",
            "reason": "; ".join(missing) or "Insufficient objective follow-up data.",
            "flags": flags,
        }

    upside = max(0.0, mfe_pct)
    downside = abs(min(0.0, mae_pct))
    giveback_ratio = ((upside - return_pct) / upside) if upside > 0 else 0.0

    if upside >= 8.0 and return_pct <= 1.0 and giveback_ratio >= 0.70:
        label = "SPIKE THEN REVERSAL"
        reason = (
            f"Price reached {mfe_pct:+.2f}% above reference but finished "
            f"{return_pct:+.2f}%, giving back {giveback_ratio * 100:.0f}% of the upside excursion."
        )
    elif upside >= 8.0 and downside >= 8.0:
        label = "TWO-SIDED VOLATILITY"
        reason = (
            f"Observed both a {mfe_pct:+.2f}% upside excursion and a {mae_pct:+.2f}% "
            f"downside excursion within {target} minutes; final return {return_pct:+.2f}%."
        )
    elif return_pct <= -8.0 or (return_pct <= -4.0 and mae_pct <= -10.0):
        label = "STRONG DOWN MOVE"
        reason = (
            f"Finished {return_pct:+.2f}% from reference with a worst observed move of "
            f"{mae_pct:+.2f}%."
        )
    elif return_pct >= 8.0 and mfe_pct >= 10.0:
        label = "STRONG UP CONTINUATION"
        reason = (
            f"Finished {return_pct:+.2f}% above reference and reached {mfe_pct:+.2f}% MFE "
            f"within {target} minutes."
        )
    elif return_pct >= 2.0:
        label = "MODEST UP CONTINUATION"
        reason = (
            f"Finished {return_pct:+.2f}% above reference; MFE {mfe_pct:+.2f}% and "
            f"MAE {mae_pct:+.2f}%."
        )
    elif return_pct <= -2.0:
        label = "NEGATIVE OUTCOME"
        reason = (
            f"Finished {return_pct:+.2f}% below reference; MFE {mfe_pct:+.2f}% and "
            f"MAE {mae_pct:+.2f}%."
        )
    else:
        label = "MIXED / RANGE"
        reason = (
            f"Finished near reference at {return_pct:+.2f}% with MFE {mfe_pct:+.2f}% "
            f"and MAE {mae_pct:+.2f}%."
        )

    expected_samples_for_high_confidence = max(10, target * 2)
    if coverage_pct >= 95.0 and sample_count >= expected_samples_for_high_confidence:
        confidence = "HIGH"
    elif coverage_pct >= 90.0 and sample_count >= 10:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    return {
        "label": label,
        "confidence": confidence,
        "reason": reason,
        "flags": flags,
    }


class AutomaticOutcomeStore:
    """Persist automatic objective outcomes for market sessions and AI reviews."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.market_store = MarketDataStore(self.database_path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=3.0)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS automatic_outcomes (
                    scope TEXT NOT NULL,
                    subject_id INTEGER NOT NULL,
                    ticker TEXT NOT NULL,
                    reference_at TEXT NOT NULL,
                    horizon_minutes INTEGER NOT NULL,
                    classified_at TEXT NOT NULL,
                    label TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    halt_count INTEGER NOT NULL DEFAULT 0,
                    flags_json TEXT NOT NULL DEFAULT '[]',
                    metrics_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY(scope, subject_id, horizon_minutes)
                );
                CREATE INDEX IF NOT EXISTS idx_automatic_outcomes_ticker
                    ON automatic_outcomes(ticker, horizon_minutes, classified_at DESC);
                CREATE INDEX IF NOT EXISTS idx_automatic_outcomes_label
                    ON automatic_outcomes(scope, horizon_minutes, label);
                """
            )

    def _halt_count(self, *, ticker: str, reference_at: str, horizon_minutes: int) -> int:
        reference = _parse_time(reference_at)
        if reference is None:
            return 0
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT received_at
                    FROM scanner_events
                    WHERE UPPER(ticker)=? AND channel='halt-scanner'
                    ORDER BY received_at ASC, id ASC
                    """,
                    (ticker.upper().strip(),),
                ).fetchall()
        except sqlite3.OperationalError:
            return 0

        horizon_seconds = max(1, int(horizon_minutes)) * 60
        count = 0
        for row in rows:
            event_time = _parse_time(row["received_at"])
            if event_time is None:
                continue
            delta = _seconds_between(event_time, reference)
            if 0 <= delta <= horizon_seconds:
                count += 1
        return count

    def _upsert(
        self,
        *,
        scope: str,
        subject_id: int,
        ticker: str,
        reference_at: str,
        horizon_minutes: int,
        metrics: dict[str, Any],
    ) -> bool:
        halt_count = self._halt_count(
            ticker=ticker,
            reference_at=reference_at,
            horizon_minutes=horizon_minutes,
        )
        classification = classify_market_path(metrics, halt_count=halt_count)
        metrics_json = json.dumps(metrics, ensure_ascii=False, sort_keys=True)
        flags_json = json.dumps(classification["flags"], ensure_ascii=False)
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO automatic_outcomes (
                    scope, subject_id, ticker, reference_at, horizon_minutes,
                    classified_at, label, confidence, reason, halt_count,
                    flags_json, metrics_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scope, subject_id, horizon_minutes) DO UPDATE SET
                    ticker=excluded.ticker,
                    reference_at=excluded.reference_at,
                    classified_at=excluded.classified_at,
                    label=excluded.label,
                    confidence=excluded.confidence,
                    reason=excluded.reason,
                    halt_count=excluded.halt_count,
                    flags_json=excluded.flags_json,
                    metrics_json=excluded.metrics_json
                WHERE automatic_outcomes.metrics_json <> excluded.metrics_json
                   OR automatic_outcomes.label <> excluded.label
                   OR automatic_outcomes.confidence <> excluded.confidence
                   OR automatic_outcomes.flags_json <> excluded.flags_json
                """,
                (
                    scope,
                    int(subject_id),
                    ticker.upper().strip(),
                    reference_at,
                    int(horizon_minutes),
                    _now_iso(),
                    classification["label"],
                    classification["confidence"],
                    classification["reason"],
                    int(halt_count),
                    flags_json,
                    metrics_json,
                ),
            )
            return bool(cursor.rowcount)

    @staticmethod
    def _decode_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        try:
            flags = json.loads(row["flags_json"] or "[]")
        except (json.JSONDecodeError, TypeError):
            flags = []
        try:
            metrics = json.loads(row["metrics_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            metrics = {}
        result = dict(row)
        result["flags"] = flags if isinstance(flags, list) else []
        result["metrics"] = metrics if isinstance(metrics, dict) else {}
        return result

    def get_outcome(
        self,
        *,
        scope: str,
        subject_id: int,
        horizon_minutes: int,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM automatic_outcomes
                WHERE scope=? AND subject_id=? AND horizon_minutes=?
                """,
                (scope, int(subject_id), int(horizon_minutes)),
            ).fetchone()
        return self._decode_row(row)

    def list_outcomes(
        self,
        *,
        scope: str,
        subject_id: int,
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM automatic_outcomes
                WHERE scope=? AND subject_id=?
                ORDER BY horizon_minutes ASC
                """,
                (scope, int(subject_id)),
            ).fetchall()
        return [item for row in rows if (item := self._decode_row(row)) is not None]

    def refresh_due_session_outcomes(self, limit: int = 200) -> int:
        changed = 0
        for session in self.market_store.list_sessions(limit=limit):
            session_id = int(session["id"])
            for horizon in TRACKING_HORIZONS:
                metrics = self.market_store.session_horizon_metrics(
                    session_id=session_id,
                    horizon_minutes=horizon,
                )
                if not metrics.get("horizon_complete"):
                    continue
                reference_at = str(
                    metrics.get("reference_captured_at")
                    or session.get("reference_captured_at")
                    or session.get("started_at")
                    or ""
                )
                if not reference_at:
                    continue
                changed += int(
                    self._upsert(
                        scope=SCOPE_MARKET_SESSION,
                        subject_id=session_id,
                        ticker=str(session.get("ticker") or ""),
                        reference_at=reference_at,
                        horizon_minutes=horizon,
                        metrics=metrics,
                    )
                )
        return changed

    def refresh_due_review_outcomes(self, limit: int = 200) -> int:
        try:
            with self._connect() as connection:
                reviews = connection.execute(
                    """
                    SELECT id, ticker, created_at
                    FROM ai_reviews
                    ORDER BY created_at DESC, id DESC
                    LIMIT ?
                    """,
                    (max(1, int(limit)),),
                ).fetchall()
        except sqlite3.OperationalError:
            return 0

        changed = 0
        for review in reviews:
            review_id = int(review["id"])
            ticker = str(review["ticker"] or "").upper().strip()
            reference_at = str(review["created_at"] or "")
            if not ticker or not reference_at:
                continue
            for horizon in TRACKING_HORIZONS:
                metrics = self.market_store.review_metrics(
                    ticker=ticker,
                    review_created_at=reference_at,
                    horizon_minutes=horizon,
                )
                if not metrics.get("horizon_complete"):
                    continue
                changed += int(
                    self._upsert(
                        scope=SCOPE_AI_REVIEW,
                        subject_id=review_id,
                        ticker=ticker,
                        reference_at=reference_at,
                        horizon_minutes=horizon,
                        metrics=metrics,
                    )
                )
        return changed

    def refresh_all_due_outcomes(self, limit: int = 200) -> dict[str, int]:
        return {
            "session_changes": self.refresh_due_session_outcomes(limit=limit),
            "review_changes": self.refresh_due_review_outcomes(limit=limit),
        }

    def count_outcomes(self, *, scope: str, horizon_minutes: int | None = None) -> int:
        query = "SELECT COUNT(*) FROM automatic_outcomes WHERE scope=?"
        params: list[Any] = [scope]
        if horizon_minutes is not None:
            query += " AND horizon_minutes=?"
            params.append(int(horizon_minutes))
        with self._connect() as connection:
            row = connection.execute(query, params).fetchone()
        return int(row[0] or 0) if row else 0
