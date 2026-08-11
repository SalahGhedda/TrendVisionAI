from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .live_pipeline import LivePipelineStore


MANUAL_RESULTS = {"OPEN", "WIN", "LOSS"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class TradeAlertJournalStore:
    """Persistent journal for user-facing FINAL_TRADE_ALERT events.

    The alert itself is created by the automated pipeline. WIN/LOSS is a manual
    user label kept separate from the objective post-plan evaluation tables.
    """

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
                CREATE TABLE IF NOT EXISTS trade_alert_journal (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    live_event_id INTEGER NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    session_id INTEGER,
                    plan_id INTEGER,
                    strategy_id TEXT,
                    strategy_name TEXT,
                    entry_low REAL,
                    entry_high REAL,
                    stop_loss REAL,
                    target_1 REAL,
                    target_2 REAL,
                    risk_reward_target_1 REAL,
                    risk_reward_target_2 REAL,
                    manual_result TEXT NOT NULL DEFAULT 'OPEN',
                    result_updated_at TEXT,
                    notes TEXT NOT NULL DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS idx_trade_alert_journal_created
                    ON trade_alert_journal(created_at DESC, id DESC);
                CREATE INDEX IF NOT EXISTS idx_trade_alert_journal_ticker
                    ON trade_alert_journal(ticker, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_trade_alert_journal_result
                    ON trade_alert_journal(manual_result, created_at DESC);
                """
            )

    def sync_from_live_events(
        self,
        live_store: LivePipelineStore,
        *,
        limit: int = 1000,
    ) -> int:
        """Backfill/sync FINAL_TRADE_ALERT events without overwriting manual result."""
        changes = 0
        events = [
            event
            for event in live_store.list_events(limit=max(1, int(limit)))
            if event.get("stage") == "FINAL_TRADE_ALERT"
        ]
        with self._connect() as connection:
            for event in events:
                payload = event.get("payload") or {}
                before = connection.total_changes
                connection.execute(
                    """
                    INSERT INTO trade_alert_journal (
                        live_event_id, created_at, ticker, session_id, plan_id,
                        strategy_id, strategy_name, entry_low, entry_high,
                        stop_loss, target_1, target_2,
                        risk_reward_target_1, risk_reward_target_2
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(live_event_id) DO UPDATE SET
                        created_at=excluded.created_at,
                        ticker=excluded.ticker,
                        session_id=excluded.session_id,
                        plan_id=excluded.plan_id,
                        strategy_id=excluded.strategy_id,
                        strategy_name=excluded.strategy_name,
                        entry_low=excluded.entry_low,
                        entry_high=excluded.entry_high,
                        stop_loss=excluded.stop_loss,
                        target_1=excluded.target_1,
                        target_2=excluded.target_2,
                        risk_reward_target_1=excluded.risk_reward_target_1,
                        risk_reward_target_2=excluded.risk_reward_target_2
                    """,
                    (
                        int(event["id"]),
                        str(event.get("created_at") or _now_iso()),
                        str(event.get("ticker") or "?").upper(),
                        int(event["session_id"]) if event.get("session_id") is not None else None,
                        int(event["plan_id"]) if event.get("plan_id") is not None else None,
                        str(payload.get("strategy_id") or "") or None,
                        str(payload.get("strategy_name") or "") or None,
                        _num(payload.get("entry_low")),
                        _num(payload.get("entry_high")),
                        _num(payload.get("stop_loss")),
                        _num(payload.get("target_1")),
                        _num(payload.get("target_2")),
                        _num(payload.get("risk_reward_target_1")),
                        _num(payload.get("risk_reward_target_2")),
                    ),
                )
                if connection.total_changes > before:
                    changes += 1
        return changes

    def list_alerts(self, limit: int = 500) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM trade_alert_journal
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_alert(self, alert_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM trade_alert_journal WHERE id=?",
                (int(alert_id),),
            ).fetchone()
        return dict(row) if row is not None else None

    def set_manual_result(self, alert_id: int, result: str) -> dict[str, Any] | None:
        value = str(result or "").upper().strip()
        if value not in MANUAL_RESULTS:
            raise ValueError(f"Manual result must be one of {sorted(MANUAL_RESULTS)}")
        updated_at = None if value == "OPEN" else _now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE trade_alert_journal
                SET manual_result=?, result_updated_at=?
                WHERE id=?
                """,
                (value, updated_at, int(alert_id)),
            )
        return self.get_alert(alert_id)

    def set_notes(self, alert_id: int, notes: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE trade_alert_journal SET notes=? WHERE id=?",
                (str(notes or ""), int(alert_id)),
            )
        return self.get_alert(alert_id)

    def stats(self) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN manual_result='OPEN' THEN 1 ELSE 0 END) AS open_count,
                    SUM(CASE WHEN manual_result='WIN' THEN 1 ELSE 0 END) AS win_count,
                    SUM(CASE WHEN manual_result='LOSS' THEN 1 ELSE 0 END) AS loss_count
                FROM trade_alert_journal
                """
            ).fetchone()
        total = int(row["total"] or 0)
        open_count = int(row["open_count"] or 0)
        wins = int(row["win_count"] or 0)
        losses = int(row["loss_count"] or 0)
        resolved = wins + losses
        return {
            "total": total,
            "open": open_count,
            "wins": wins,
            "losses": losses,
            "resolved": resolved,
            "manual_win_rate_pct": (wins / resolved * 100.0) if resolved else None,
        }
