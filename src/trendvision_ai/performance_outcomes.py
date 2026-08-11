from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Any

from . import automatic_outcomes as outcomes_mod


_installed = False


def configure_sqlite_for_desktop(database_path: str | Path) -> None:
    """Use WAL so listener/market/background readers do not block the UI as often."""
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sqlite3.connect(path, timeout=5.0) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.Error:
        # Performance tuning must never prevent the application from starting.
        pass


def _fast_halt_count(
    self: Any,
    *,
    ticker: str,
    reference_at: str,
    horizon_minutes: int,
) -> int:
    reference = outcomes_mod._parse_time(reference_at)
    if reference is None:
        return 0
    deadline = reference + timedelta(minutes=max(1, int(horizon_minutes)))
    try:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*)
                FROM scanner_events
                WHERE UPPER(ticker)=?
                  AND channel='halt-scanner'
                  AND julianday(received_at) >= julianday(?)
                  AND julianday(received_at) <= julianday(?)
                """,
                (
                    ticker.upper().strip(),
                    reference.isoformat(timespec="seconds"),
                    deadline.isoformat(timespec="seconds"),
                ),
            ).fetchone()
        return int(row[0] or 0) if row else 0
    except sqlite3.OperationalError:
        return 0


def _existing_keys(self: Any, scope: str) -> set[tuple[int, int]]:
    try:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT subject_id, horizon_minutes
                FROM automatic_outcomes
                WHERE scope=?
                """,
                (scope,),
            ).fetchall()
    except sqlite3.OperationalError:
        return set()
    return {(int(row["subject_id"]), int(row["horizon_minutes"])) for row in rows}


def _fast_refresh_due_session_outcomes(self: Any, limit: int = 200) -> int:
    """Classify each completed session horizon once instead of every poll forever."""
    changed = 0
    existing = _existing_keys(self, outcomes_mod.SCOPE_MARKET_SESSION)
    for session in self.market_store.list_sessions(limit=limit):
        session_id = int(session["id"])
        for horizon in outcomes_mod.TRACKING_HORIZONS:
            key = (session_id, int(horizon))
            if key in existing:
                continue
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
                    scope=outcomes_mod.SCOPE_MARKET_SESSION,
                    subject_id=session_id,
                    ticker=str(session.get("ticker") or ""),
                    reference_at=reference_at,
                    horizon_minutes=horizon,
                    metrics=metrics,
                )
            )
            existing.add(key)
    return changed


def _fast_refresh_due_review_outcomes(self: Any, limit: int = 200) -> int:
    """Only calculate AI-review horizons that have not already been persisted."""
    existing = _existing_keys(self, outcomes_mod.SCOPE_AI_REVIEW)
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
        for horizon in outcomes_mod.TRACKING_HORIZONS:
            key = (review_id, int(horizon))
            if key in existing:
                continue
            metrics = self.market_store.review_metrics(
                ticker=ticker,
                review_created_at=reference_at,
                horizon_minutes=horizon,
            )
            if not metrics.get("horizon_complete"):
                continue
            changed += int(
                self._upsert(
                    scope=outcomes_mod.SCOPE_AI_REVIEW,
                    subject_id=review_id,
                    ticker=ticker,
                    reference_at=reference_at,
                    horizon_minutes=horizon,
                    metrics=metrics,
                )
            )
            existing.add(key)
    return changed


def install_outcome_performance_patches() -> None:
    global _installed
    if _installed:
        return
    outcomes_mod.AutomaticOutcomeStore._halt_count = _fast_halt_count
    outcomes_mod.AutomaticOutcomeStore.refresh_due_session_outcomes = _fast_refresh_due_session_outcomes
    outcomes_mod.AutomaticOutcomeStore.refresh_due_review_outcomes = _fast_refresh_due_review_outcomes
    _installed = True
