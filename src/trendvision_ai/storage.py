from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .models import CapturedNotification
from .scanner_events import ScannerEvent
from .ticker_memory import (
    TickerEventRecord,
    TickerState,
    build_ticker_state,
    convergence_summary,
)


SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at TEXT NOT NULL,
    app_name TEXT NOT NULL,
    source TEXT NOT NULL,
    channel TEXT,
    title TEXT,
    body TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    ticker TEXT,
    fingerprint TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_raw_notifications_ticker ON raw_notifications(ticker);
CREATE INDEX IF NOT EXISTS idx_raw_notifications_channel ON raw_notifications(channel);
CREATE INDEX IF NOT EXISTS idx_raw_notifications_received_at ON raw_notifications(received_at);

CREATE TABLE IF NOT EXISTS scanner_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at TEXT NOT NULL,
    channel TEXT NOT NULL,
    ticker TEXT,
    event_type TEXT NOT NULL,
    headline TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    fingerprint TEXT NOT NULL UNIQUE,
    data_json TEXT NOT NULL DEFAULT '{}',
    item_index INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_scanner_events_ticker ON scanner_events(ticker);
CREATE INDEX IF NOT EXISTS idx_scanner_events_channel ON scanner_events(channel);
CREATE INDEX IF NOT EXISTS idx_scanner_events_received_at ON scanner_events(received_at);

CREATE TABLE IF NOT EXISTS ticker_states (
    ticker TEXT PRIMARY KEY,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    event_count INTEGER NOT NULL,
    channel_count INTEGER NOT NULL,
    channels_json TEXT NOT NULL,
    latest_event_type TEXT NOT NULL,
    latest_headline TEXT NOT NULL,
    facts_json TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ticker_states_last_seen_at ON ticker_states(last_seen_at);
CREATE INDEX IF NOT EXISTS idx_ticker_states_channel_count ON ticker_states(channel_count);
"""


class AlertStore:
    def __init__(self, database_path: str | Path, jsonl_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.jsonl_path = Path(jsonl_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database_path)

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(SCHEMA)

            # Existing prototype databases predate channel-specific JSON and
            # multi-item all-in-one notifications. Migrate them in place so the
            # user does not have to delete collected alert history.
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(scanner_events)")
            }
            if "data_json" not in columns:
                connection.execute(
                    "ALTER TABLE scanner_events ADD COLUMN data_json TEXT NOT NULL DEFAULT '{}'"
                )
            if "item_index" not in columns:
                connection.execute(
                    "ALTER TABLE scanner_events ADD COLUMN item_index INTEGER NOT NULL DEFAULT 0"
                )

    def save(self, notification: CapturedNotification) -> bool:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO raw_notifications (
                        received_at, app_name, source, channel, title, body,
                        raw_text, ticker, fingerprint
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        notification.received_at,
                        notification.app_name,
                        notification.source,
                        notification.channel,
                        notification.title,
                        notification.body,
                        notification.raw_text,
                        notification.ticker,
                        notification.fingerprint,
                    ),
                )
        except sqlite3.IntegrityError:
            return False

        with self.jsonl_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(notification.to_dict(), ensure_ascii=False) + "\n")
        return True

    def save_scanner_event(self, event: ScannerEvent) -> bool:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO scanner_events (
                        received_at, channel, ticker, event_type, headline,
                        raw_text, fingerprint, data_json, item_index
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.received_at,
                        event.channel,
                        event.ticker,
                        event.event_type,
                        event.headline,
                        event.raw_text,
                        event.fingerprint,
                        json.dumps(event.data, ensure_ascii=False, sort_keys=True),
                        event.item_index,
                    ),
                )
        except sqlite3.IntegrityError:
            return False
        return True

    @staticmethod
    def _decode_data(value: str) -> dict[str, Any]:
        try:
            decoded = json.loads(value or "{}")
            return decoded if isinstance(decoded, dict) else {}
        except json.JSONDecodeError:
            return {}

    def load_ticker_events(self, ticker: str) -> list[TickerEventRecord]:
        ticker = ticker.upper().strip()
        if not ticker:
            return []

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT received_at, channel, event_type, headline, data_json
                FROM scanner_events
                WHERE UPPER(ticker) = ?
                ORDER BY received_at ASC, id ASC
                """,
                (ticker,),
            ).fetchall()

        return [
            TickerEventRecord(
                received_at=row[0],
                channel=row[1],
                event_type=row[2],
                headline=row[3],
                data=self._decode_data(row[4]),
            )
            for row in rows
        ]

    def refresh_ticker_state(self, ticker: str) -> TickerState | None:
        ticker = ticker.upper().strip()
        if not ticker:
            return None

        state = build_ticker_state(ticker, self.load_ticker_events(ticker))
        if state is None:
            return None

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO ticker_states (
                    ticker, first_seen_at, last_seen_at, event_count,
                    channel_count, channels_json, latest_event_type,
                    latest_headline, facts_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(ticker) DO UPDATE SET
                    first_seen_at=excluded.first_seen_at,
                    last_seen_at=excluded.last_seen_at,
                    event_count=excluded.event_count,
                    channel_count=excluded.channel_count,
                    channels_json=excluded.channels_json,
                    latest_event_type=excluded.latest_event_type,
                    latest_headline=excluded.latest_headline,
                    facts_json=excluded.facts_json,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    state.ticker,
                    state.first_seen_at,
                    state.last_seen_at,
                    state.event_count,
                    state.channel_count,
                    json.dumps(state.channels, ensure_ascii=False),
                    state.latest_event_type,
                    state.latest_headline,
                    json.dumps(state.facts, ensure_ascii=False, sort_keys=True),
                ),
            )
        return state

    def rebuild_ticker_states(self) -> int:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT UPPER(ticker)
                FROM scanner_events
                WHERE ticker IS NOT NULL AND TRIM(ticker) <> ''
                ORDER BY UPPER(ticker)
                """
            ).fetchall()
            connection.execute("DELETE FROM ticker_states")

        rebuilt = 0
        for row in rows:
            if self.refresh_ticker_state(row[0]) is not None:
                rebuilt += 1
        return rebuilt

    def get_convergence_summary(self, ticker: str, window_minutes: int = 30) -> dict[str, Any]:
        ticker = ticker.upper().strip()
        return convergence_summary(
            ticker,
            self.load_ticker_events(ticker),
            window_minutes=window_minutes,
        )

    def list_ticker_states(self, limit: int = 25) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT ticker, first_seen_at, last_seen_at, event_count,
                       channel_count, channels_json, latest_event_type,
                       latest_headline, facts_json
                FROM ticker_states
                ORDER BY last_seen_at DESC
                LIMIT ?
                """,
                (max(1, limit),),
            ).fetchall()

        result: list[dict[str, Any]] = []
        for row in rows:
            result.append(
                {
                    "ticker": row[0],
                    "first_seen_at": row[1],
                    "last_seen_at": row[2],
                    "event_count": row[3],
                    "channel_count": row[4],
                    "channels": json.loads(row[5] or "[]"),
                    "latest_event_type": row[6],
                    "latest_headline": row[7],
                    "facts": self._decode_data(row[8]),
                }
            )
        return result
