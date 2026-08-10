from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


KEYRING_SERVICE = "TrendVisionAI"
KEYRING_ACCOUNT = "openai_api_key"
DEFAULT_MODEL = "gpt-5-mini"


@dataclass(slots=True)
class AIReviewResult:
    ticker: str
    model: str
    verdict: str
    confidence: str
    summary: str
    positive_factors: list[str]
    risk_factors: list[str]
    missing_information: list[str]
    next_signals_to_watch: list[str]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def get_api_key() -> str | None:
    """Return the API key without storing it in project files."""
    env_key = os.getenv("OPENAI_API_KEY", "").strip()
    if env_key:
        return env_key
    try:
        import keyring

        value = keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
        return value.strip() if value else None
    except Exception:
        return None


def save_api_key(api_key: str) -> None:
    value = api_key.strip()
    if not value:
        raise ValueError("API key cannot be empty.")
    import keyring

    keyring.set_password(KEYRING_SERVICE, KEYRING_ACCOUNT, value)


def delete_api_key() -> None:
    try:
        import keyring

        keyring.delete_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
    except Exception:
        pass


def build_review_snapshot(
    *,
    ticker: str,
    state: dict[str, Any],
    convergence: dict[str, Any],
    attention: dict[str, Any],
) -> dict[str, Any]:
    """Create the compact case file sent to the model.

    Only data already captured from TrendVision is included. Missing data stays
    missing; this layer deliberately performs no external market-data lookup.
    """
    facts: dict[str, Any] = {}
    for key, entry in (state.get("facts") or {}).items():
        if key in {"raw_payload", "ticker"}:
            continue
        if isinstance(entry, dict):
            facts[key] = {
                "value": entry.get("value"),
                "source_channel": entry.get("source_channel"),
                "received_at": entry.get("received_at"),
            }
        else:
            facts[key] = entry

    events = []
    for event in convergence.get("events") or []:
        events.append(
            {
                "received_at": event.get("received_at"),
                "channel": event.get("channel"),
                "event_type": event.get("event_type"),
                "headline": event.get("headline"),
                "data": event.get("data") or {},
            }
        )

    return {
        "ticker": ticker.upper(),
        "data_source": "TrendVision Discord alerts captured from Windows notifications",
        "important_limitations": [
            "Discord Windows notifications omit some lower fields from rich embeds.",
            "No external market-price, chart, news, SEC, borrow, or brokerage API is queried.",
            "Missing fields are unknown and must not be inferred.",
            "The attention score is a local review-priority heuristic, not a buy/sell score.",
            "For volume/squeeze notifications, the displayed percentage is the alert price move; it is not relative volume unless an explicit RV/relative_volume field exists.",
        ],
        "ticker_memory": {
            "first_seen_at": state.get("first_seen_at"),
            "last_seen_at": state.get("last_seen_at"),
            "event_count": state.get("event_count"),
            "channel_count": state.get("channel_count"),
            "channels": state.get("channels") or [],
            "latest_event_type": state.get("latest_event_type"),
            "latest_headline": state.get("latest_headline"),
            "latest_known_facts": facts,
        },
        "recent_convergence": {
            "window_minutes": convergence.get("window_minutes"),
            "event_count": convergence.get("event_count"),
            "channel_count": convergence.get("channel_count"),
            "channels": convergence.get("channels") or [],
            "events": events,
        },
        "local_attention": attention,
    }


_REVIEW_SCHEMA = {
    "type": "json_schema",
    "name": "trendvision_candidate_review",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "verdict": {
                "type": "string",
                "enum": ["IGNORE", "WATCH", "POTENTIAL SETUP", "HIGH RISK"],
            },
            "confidence": {
                "type": "string",
                "enum": ["LOW", "MEDIUM", "HIGH"],
            },
            "summary": {"type": "string"},
            "positive_factors": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 8,
            },
            "risk_factors": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 8,
            },
            "missing_information": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 8,
            },
            "next_signals_to_watch": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 8,
            },
        },
        "required": [
            "verdict",
            "confidence",
            "summary",
            "positive_factors",
            "risk_factors",
            "missing_information",
            "next_signals_to_watch",
        ],
    },
}


_INSTRUCTIONS = """You are the candidate-review layer of TrendVisionAI.
Analyze ONLY the supplied TrendVision scanner case file. Do not browse, invent current market data, or fill missing fields from memory.

Your purpose is prioritization, not automatic execution. Evaluate whether the scanner evidence is internally interesting enough for a human to investigate further.

Interpretation rules:
- Independent scanner convergence is stronger evidence than repeated alerts from only one scanner.
- Treat explicit MOMENTUM/BREAKOUT, explicit relative volume, low float, news, squeeze/zero-borrow, whale direction, and halts according to the supplied data only.
- A ticker already showing a very large positive move can be interesting but also carries chase/late-entry risk.
- A halt or whale-down alert should be surfaced clearly as risk.
- Do not treat an alert's percentage move as relative volume unless the case file explicitly contains RV or relative_volume.
- The local attention score is only a triage heuristic and must not control your verdict.
- If important information is absent because Windows truncated the Discord embed, say it is missing rather than assuming it.
- Do not claim that a trade will be profitable and do not issue an automatic order instruction.

Return a concise structured review."""


def analyze_snapshot(
    snapshot: dict[str, Any],
    *,
    api_key: str,
    model: str = DEFAULT_MODEL,
) -> AIReviewResult:
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    response = client.responses.create(
        model=model,
        instructions=_INSTRUCTIONS,
        input=json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
        text={"format": _REVIEW_SCHEMA},
    )
    parsed = json.loads(response.output_text)
    ticker = str(snapshot.get("ticker") or "?").upper()
    return AIReviewResult(
        ticker=ticker,
        model=model,
        verdict=str(parsed["verdict"]),
        confidence=str(parsed["confidence"]),
        summary=str(parsed["summary"]),
        positive_factors=[str(value) for value in parsed.get("positive_factors") or []],
        risk_factors=[str(value) for value in parsed.get("risk_factors") or []],
        missing_information=[str(value) for value in parsed.get("missing_information") or []],
        next_signals_to_watch=[str(value) for value in parsed.get("next_signals_to_watch") or []],
        created_at=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    )


class AIReviewStore:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database_path, timeout=3.0)

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS ai_reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    model TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_ai_reviews_ticker_created ON ai_reviews(ticker, created_at DESC)"
            )

    def save(self, result: AIReviewResult, snapshot: dict[str, Any]) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO ai_reviews (
                    ticker, created_at, model, verdict, confidence, summary,
                    result_json, snapshot_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.ticker,
                    result.created_at,
                    result.model,
                    result.verdict,
                    result.confidence,
                    result.summary,
                    json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True),
                    json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
                ),
            )

    def latest(self, ticker: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT result_json
                FROM ai_reviews
                WHERE UPPER(ticker) = ?
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (ticker.upper().strip(),),
            ).fetchone()
        if row is None:
            return None
        try:
            value = json.loads(row[0])
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            return None
