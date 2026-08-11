from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


class OpenAIApiCallStore:
    """Persistent audit log for OpenAI requests made by TrendVisionAI."""

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
                CREATE TABLE IF NOT EXISTS openai_api_calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    updated_at TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    model TEXT NOT NULL,
                    reasoning_effort TEXT,
                    strategy_id TEXT,
                    strategy_name TEXT,
                    strategy_score INTEGER,
                    setup_instance_key TEXT,
                    status TEXT NOT NULL DEFAULT 'IN PROGRESS',
                    duration_ms INTEGER,
                    decision TEXT,
                    error_text TEXT NOT NULL DEFAULT '',
                    response_text TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_openai_api_calls_started
                    ON openai_api_calls(started_at DESC, id DESC);
                CREATE INDEX IF NOT EXISTS idx_openai_api_calls_ticker
                    ON openai_api_calls(ticker, started_at DESC);
                CREATE INDEX IF NOT EXISTS idx_openai_api_calls_status
                    ON openai_api_calls(status, started_at DESC);
                """
            )
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(openai_api_calls)")
            }
            if "response_text" not in columns:
                connection.execute(
                    "ALTER TABLE openai_api_calls ADD COLUMN response_text TEXT NOT NULL DEFAULT ''"
                )
            if "setup_instance_key" not in columns:
                connection.execute(
                    "ALTER TABLE openai_api_calls ADD COLUMN setup_instance_key TEXT"
                )

    def start_call(
        self,
        *,
        ticker: str,
        purpose: str,
        model: str,
        reasoning_effort: str | None = None,
        strategy_id: str | None = None,
        strategy_name: str | None = None,
        strategy_score: int | None = None,
        setup_instance_key: str | None = None,
    ) -> int:
        now = _now_iso()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO openai_api_calls (
                    started_at, updated_at, ticker, purpose, model,
                    reasoning_effort, strategy_id, strategy_name, strategy_score,
                    setup_instance_key, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'IN PROGRESS')
                """,
                (
                    now,
                    now,
                    ticker.upper().strip() or "?",
                    str(purpose or "UNKNOWN"),
                    str(model or "UNKNOWN"),
                    str(reasoning_effort or "") or None,
                    str(strategy_id or "") or None,
                    str(strategy_name or "") or None,
                    int(strategy_score) if strategy_score is not None else None,
                    str(setup_instance_key or "") or None,
                ),
            )
            return int(cursor.lastrowid)

    def finish_call(
        self,
        call_id: int,
        *,
        status: str,
        duration_ms: int | None = None,
        decision: str | None = None,
        error_text: str = "",
        response_text: str = "",
    ) -> None:
        now = _now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE openai_api_calls
                SET completed_at=?, updated_at=?, status=?, duration_ms=?,
                    decision=?, error_text=?, response_text=?
                WHERE id=?
                """,
                (
                    now,
                    now,
                    str(status or "UNKNOWN"),
                    int(duration_ms) if duration_ms is not None else None,
                    str(decision or "") or None,
                    str(error_text or "")[:1000],
                    str(response_text or ""),
                    int(call_id),
                ),
            )

    def list_calls(self, limit: int = 500) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM openai_api_calls
                ORDER BY started_at DESC, id DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def stats(self) -> dict[str, Any]:
        today = datetime.now().astimezone().date().isoformat()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN substr(started_at, 1, 10)=? THEN 1 ELSE 0 END) AS today_count,
                    SUM(CASE WHEN status='COMPLETED' THEN 1 ELSE 0 END) AS completed,
                    SUM(CASE WHEN status='FAILED' THEN 1 ELSE 0 END) AS failed,
                    SUM(CASE WHEN status='IN PROGRESS' THEN 1 ELSE 0 END) AS in_progress
                FROM openai_api_calls
                """,
                (today,),
            ).fetchone()
        return {
            "total": int(row["total"] or 0),
            "today": int(row["today_count"] or 0),
            "completed": int(row["completed"] or 0),
            "failed": int(row["failed"] or 0),
            "in_progress": int(row["in_progress"] or 0),
        }

    def revision(self) -> tuple[int, int, str]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*), COALESCE(MAX(id), 0), COALESCE(MAX(updated_at), '')
                FROM openai_api_calls
                """
            ).fetchone()
        return int(row[0] or 0), int(row[1] or 0), str(row[2] or "")
