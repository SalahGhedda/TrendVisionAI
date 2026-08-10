from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .market_data import MarketDataStore


OUTCOME_OPTIONS = [
    "NOT LABELED",
    "STRONG CONTINUATION",
    "MODEST CONTINUATION",
    "FAILED / REVERSED",
    "NO CLEAN SETUP",
    "TOO RISKY / UNTRADEABLE",
    "NOT ENOUGH FOLLOW-UP",
]

HORIZON_OPTIONS = {
    "15 min": 15,
    "30 min": 30,
    "60 min": 60,
    "4 hours": 240,
}


def _parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _decode_json(value: Any, fallback: Any) -> Any:
    try:
        decoded = json.loads(value or "")
        return decoded
    except (json.JSONDecodeError, TypeError):
        return fallback


class ReviewOutcomeStore:
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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS ai_review_outcomes (
                    review_id INTEGER PRIMARY KEY,
                    ticker TEXT NOT NULL,
                    labeled_at TEXT NOT NULL,
                    horizon_minutes INTEGER NOT NULL DEFAULT 30,
                    outcome TEXT NOT NULL,
                    notes TEXT NOT NULL DEFAULT '',
                    followup_event_count INTEGER NOT NULL DEFAULT 0,
                    followup_channel_count INTEGER NOT NULL DEFAULT 0,
                    followup_channels_json TEXT NOT NULL DEFAULT '[]',
                    market_metrics_json TEXT NOT NULL DEFAULT '{}',
                    market_reference_price REAL,
                    market_return_pct REAL,
                    market_mfe_pct REAL,
                    market_mae_pct REAL,
                    market_sample_count INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(ai_review_outcomes)")
            }
            migrations = {
                "market_metrics_json": "TEXT NOT NULL DEFAULT '{}'",
                "market_reference_price": "REAL",
                "market_return_pct": "REAL",
                "market_mfe_pct": "REAL",
                "market_mae_pct": "REAL",
                "market_sample_count": "INTEGER NOT NULL DEFAULT 0",
            }
            for column, definition in migrations.items():
                if column not in columns:
                    connection.execute(
                        f"ALTER TABLE ai_review_outcomes ADD COLUMN {column} {definition}"
                    )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_ai_review_outcomes_ticker ON ai_review_outcomes(ticker, labeled_at DESC)"
            )

    def latest_review_record(self, ticker: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, ticker, created_at, model, result_json
                FROM ai_reviews
                WHERE UPPER(ticker) = ?
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (ticker.upper().strip(),),
            ).fetchone()
        if row is None:
            return None
        result = _decode_json(row["result_json"], {})
        return {
            "id": int(row["id"]),
            "ticker": row["ticker"],
            "created_at": row["created_at"],
            "model": row["model"],
            "result": result if isinstance(result, dict) else {},
        }

    def post_review_summary(
        self,
        *,
        ticker: str,
        review_created_at: str,
        horizon_minutes: int = 30,
    ) -> dict[str, Any]:
        review_time = _parse_time(review_created_at)
        if review_time is None:
            return {"event_count": 0, "channel_count": 0, "channels": [], "events": []}

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT received_at, channel, event_type, headline, data_json
                FROM scanner_events
                WHERE UPPER(ticker) = ?
                ORDER BY received_at ASC, id ASC
                """,
                (ticker.upper().strip(),),
            ).fetchall()

        events: list[dict[str, Any]] = []
        horizon_seconds = max(1, horizon_minutes) * 60
        for row in rows:
            event_time = _parse_time(row["received_at"])
            if event_time is None:
                continue
            try:
                delta = (event_time - review_time).total_seconds()
            except TypeError:
                delta = (
                    event_time.replace(tzinfo=None) - review_time.replace(tzinfo=None)
                ).total_seconds()
            if delta <= 0 or delta > horizon_seconds:
                continue
            data = _decode_json(row["data_json"], {})
            events.append(
                {
                    "received_at": row["received_at"],
                    "channel": row["channel"],
                    "event_type": row["event_type"],
                    "headline": row["headline"],
                    "data": data if isinstance(data, dict) else {},
                }
            )

        channels: list[str] = []
        for event in events:
            channel = str(event.get("channel") or "")
            if channel and channel not in channels:
                channels.append(channel)
        return {
            "event_count": len(events),
            "channel_count": len(channels),
            "channels": channels,
            "events": events,
        }

    def market_summary(
        self,
        *,
        ticker: str,
        review_created_at: str,
        horizon_minutes: int = 30,
    ) -> dict[str, Any]:
        return self.market_store.review_metrics(
            ticker=ticker,
            review_created_at=review_created_at,
            horizon_minutes=horizon_minutes,
        )

    def save_outcome(
        self,
        *,
        review_id: int,
        ticker: str,
        horizon_minutes: int,
        outcome: str,
        notes: str,
        followup: dict[str, Any],
        market_metrics: dict[str, Any] | None = None,
    ) -> None:
        labeled_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        market = market_metrics if isinstance(market_metrics, dict) else {}
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO ai_review_outcomes (
                    review_id, ticker, labeled_at, horizon_minutes, outcome, notes,
                    followup_event_count, followup_channel_count, followup_channels_json,
                    market_metrics_json, market_reference_price, market_return_pct,
                    market_mfe_pct, market_mae_pct, market_sample_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(review_id) DO UPDATE SET
                    labeled_at=excluded.labeled_at,
                    horizon_minutes=excluded.horizon_minutes,
                    outcome=excluded.outcome,
                    notes=excluded.notes,
                    followup_event_count=excluded.followup_event_count,
                    followup_channel_count=excluded.followup_channel_count,
                    followup_channels_json=excluded.followup_channels_json,
                    market_metrics_json=excluded.market_metrics_json,
                    market_reference_price=excluded.market_reference_price,
                    market_return_pct=excluded.market_return_pct,
                    market_mfe_pct=excluded.market_mfe_pct,
                    market_mae_pct=excluded.market_mae_pct,
                    market_sample_count=excluded.market_sample_count
                """,
                (
                    int(review_id),
                    ticker.upper().strip(),
                    labeled_at,
                    int(horizon_minutes),
                    outcome,
                    notes.strip(),
                    int(followup.get("event_count") or 0),
                    int(followup.get("channel_count") or 0),
                    json.dumps(followup.get("channels") or [], ensure_ascii=False),
                    json.dumps(market, ensure_ascii=False, sort_keys=True),
                    market.get("reference_price"),
                    market.get("return_pct"),
                    market.get("mfe_pct"),
                    market.get("mae_pct"),
                    int(market.get("sample_count") or 0),
                ),
            )

    def get_outcome(self, review_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT review_id, ticker, labeled_at, horizon_minutes, outcome, notes,
                       followup_event_count, followup_channel_count, followup_channels_json,
                       market_metrics_json, market_reference_price, market_return_pct,
                       market_mfe_pct, market_mae_pct, market_sample_count
                FROM ai_review_outcomes
                WHERE review_id = ?
                """,
                (int(review_id),),
            ).fetchone()
        if row is None:
            return None
        channels = _decode_json(row["followup_channels_json"], [])
        market_metrics = _decode_json(row["market_metrics_json"], {})
        return {
            "review_id": int(row["review_id"]),
            "ticker": row["ticker"],
            "labeled_at": row["labeled_at"],
            "horizon_minutes": int(row["horizon_minutes"]),
            "outcome": row["outcome"],
            "notes": row["notes"],
            "followup_event_count": int(row["followup_event_count"]),
            "followup_channel_count": int(row["followup_channel_count"]),
            "followup_channels": channels if isinstance(channels, list) else [],
            "market_metrics": market_metrics if isinstance(market_metrics, dict) else {},
            "market_reference_price": row["market_reference_price"],
            "market_return_pct": row["market_return_pct"],
            "market_mfe_pct": row["market_mfe_pct"],
            "market_mae_pct": row["market_mae_pct"],
            "market_sample_count": int(row["market_sample_count"] or 0),
        }

    def list_reviews(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT r.id, r.ticker, r.created_at, r.model, r.result_json,
                       o.outcome, o.horizon_minutes, o.notes,
                       o.followup_event_count, o.followup_channel_count,
                       o.followup_channels_json, o.market_metrics_json,
                       o.market_reference_price, o.market_return_pct,
                       o.market_mfe_pct, o.market_mae_pct, o.market_sample_count
                FROM ai_reviews r
                LEFT JOIN ai_review_outcomes o ON o.review_id = r.id
                ORDER BY r.created_at DESC, r.id DESC
                LIMIT ?
                """,
                (max(1, limit),),
            ).fetchall()

        result: list[dict[str, Any]] = []
        for row in rows:
            review = _decode_json(row["result_json"], {})
            market_metrics = _decode_json(row["market_metrics_json"], {})
            result.append(
                {
                    "id": int(row["id"]),
                    "ticker": row["ticker"],
                    "created_at": row["created_at"],
                    "model": row["model"],
                    "review": review if isinstance(review, dict) else {},
                    "outcome": row["outcome"] or "NOT LABELED",
                    "horizon_minutes": row["horizon_minutes"],
                    "notes": row["notes"] or "",
                    "followup_event_count": int(row["followup_event_count"] or 0),
                    "followup_channel_count": int(row["followup_channel_count"] or 0),
                    "market_metrics": market_metrics if isinstance(market_metrics, dict) else {},
                    "market_reference_price": row["market_reference_price"],
                    "market_return_pct": row["market_return_pct"],
                    "market_mfe_pct": row["market_mfe_pct"],
                    "market_mae_pct": row["market_mae_pct"],
                    "market_sample_count": int(row["market_sample_count"] or 0),
                }
            )
        return result
