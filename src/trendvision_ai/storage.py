from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import CapturedNotification


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
