from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


KEYRING_SERVICE = "TrendVisionAI"
KEYRING_ACCOUNT = "openai_api_key"
DEFAULT_MODEL = "gpt-5-mini"
REVIEW_VERSION = 2


@dataclass(slots=True)
class AIReviewResult:
    ticker: str
    model: str
    interest_level: str
    risk_level: str
    evidence_quality: str
    review_status: str
    summary: str
    positive_factors: list[str]
    risk_factors: list[str]
    data_conflicts: list[str]
    missing_information: list[str]
    next_signals_to_watch: list[str]
    invalidation_warnings: list[str]
    created_at: str
    review_version: int = REVIEW_VERSION

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
        "review_version": REVIEW_VERSION,
        "data_source": "TrendVision Discord alerts captured from Windows notifications",
        "important_limitations": [
            "Discord Windows notifications omit some lower fields from rich embeds.",
            "No external market-price, chart, news, SEC, borrow, options, or brokerage API is queried.",
            "Missing fields are unknown and must not be inferred.",
            "The attention score is a local review-priority heuristic, not a buy/sell score.",
            "For volume/squeeze notifications, the displayed percentage is the alert price move; it is not relative volume unless an explicit RV/relative_volume field exists.",
            "A country flag or issuer origin is descriptive metadata, not automatically a positive or negative trading factor.",
            "Market capitalization does not by itself prove liquidity.",
            "Labels such as Known Runner are descriptive metadata and are not independent evidence of future performance.",
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
    "name": "trendvision_candidate_review_v2",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "interest_level": {
                "type": "string",
                "enum": ["LOW", "MEDIUM", "HIGH", "VERY HIGH"],
            },
            "risk_level": {
                "type": "string",
                "enum": ["LOW", "MODERATE", "HIGH", "EXTREME"],
            },
            "evidence_quality": {
                "type": "string",
                "enum": ["LOW", "MEDIUM", "HIGH"],
            },
            "review_status": {
                "type": "string",
                "enum": [
                    "IGNORE",
                    "WATCH",
                    "WAIT FOR CONFIRMATION",
                    "POTENTIAL SETUP",
                    "AVOID",
                ],
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
            "data_conflicts": {
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
            "invalidation_warnings": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 8,
            },
        },
        "required": [
            "interest_level",
            "risk_level",
            "evidence_quality",
            "review_status",
            "summary",
            "positive_factors",
            "risk_factors",
            "data_conflicts",
            "missing_information",
            "next_signals_to_watch",
            "invalidation_warnings",
        ],
    },
}


_INSTRUCTIONS = """You are the candidate-review layer of TrendVisionAI.
Analyze ONLY the supplied TrendVision scanner case file. Do not browse, invent current market data, or fill missing fields from memory.

Your purpose is prioritization for human review, not automatic execution. Separate these concepts explicitly:
- INTEREST: how unusual / worthy of attention the scanner convergence is.
- RISK: how dangerous, extended, volatile, halted, contradictory, or chase-prone the situation is.
- EVIDENCE QUALITY: how complete and internally consistent the captured evidence is.
- REVIEW STATUS: what the human should do with the case inside TrendVisionAI (ignore, watch, wait for confirmation, potential setup, or avoid). This is not an order instruction.

Interpretation rules:
- Independent scanner convergence is stronger evidence than repeated alerts from only one scanner.
- Treat explicit MOMENTUM/BREAKOUT, explicit relative volume, float, news, squeeze/zero-borrow, whale direction, and halts according to the supplied data only.
- Very large positive moves increase interest but also materially increase chase/late-entry risk. Do not let excitement lower the risk rating.
- Halts are a major volatility/risk factor. Multiple halts in the review window should generally produce HIGH or EXTREME risk.
- A country flag or issuer origin alone is neutral. Do NOT call China, another country, ADR status, or foreign origin a risk unless the supplied case file contains a concrete listing/regulatory problem.
- Market cap is not a liquidity measurement. Do NOT infer a "liquidity profile" from market cap alone.
- Phrases such as "Known Runner" are descriptive labels, not independent positive evidence and not proof of future performance.
- Do not treat an alert's percentage move as relative volume unless the case file explicitly contains RV or relative_volume.
- If structured fields conflict with alert text, put that in data_conflicts and reduce evidence quality rather than choosing whichever value is more exciting.
- The local attention score is only a triage heuristic and must not control the review status.
- If important information is absent because Windows truncated the Discord embed, say it is missing rather than assuming it.
- next_signals_to_watch should focus on subsequent TrendVision signals that this application can actually capture. Do not request options flow, live order-book data, or another external feed as if it were currently integrated.
- A POTENTIAL SETUP status requires sufficiently consistent evidence. If risk is EXTREME or major data conflicts remain unresolved, prefer WAIT FOR CONFIRMATION or AVOID even when interest is VERY HIGH.
- Do not claim that a trade will be profitable and do not issue an automatic order instruction.

Return a concise structured review."""


_RISK_ORDER = {"LOW": 0, "MODERATE": 1, "HIGH": 2, "EXTREME": 3}
_EVIDENCE_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace("$", "").replace(",", "")
    text = text.removesuffix("%").removesuffix("x").strip()
    try:
        return float(text)
    except ValueError:
        return None


def _events(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    return list((snapshot.get("recent_convergence") or {}).get("events") or [])


def _minimum_level(value: str, minimum: str, order: dict[str, int]) -> str:
    value = value if value in order else minimum
    return minimum if order[value] < order[minimum] else value


def _maximum_level(value: str, maximum: str, order: dict[str, int]) -> str:
    value = value if value in order else maximum
    return maximum if order[value] > order[maximum] else value


def _detect_local_conflicts(snapshot: dict[str, Any]) -> list[str]:
    """Find simple contradictions that can be checked without AI judgment."""
    conflicts: list[str] = []
    for event in _events(snapshot):
        data = event.get("data") or {}
        searchable = "\n".join(
            str(value)
            for value in (
                event.get("headline"),
                data.get("raw_payload"),
            )
            if value
        ).casefold()
        zero_borrow = data.get("zero_borrow")
        if zero_borrow is False and (
            "0 borrow" in searchable or "no shares available to borrow" in searchable
        ):
            conflicts.append(
                "Alert text indicates 0 Borrow / no shares available while structured zero_borrow is false."
            )
    return list(dict.fromkeys(conflicts))


def calibrate_review_payload(
    parsed: dict[str, Any],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Apply deterministic guardrails after the model review.

    The model still does the qualitative reasoning, but obvious facts such as
    repeated halts or a >100% move cannot accidentally result in a low risk
    rating. This also prevents an EXTREME-risk case from being labeled a clean
    potential setup before more confirmation arrives.
    """
    result = dict(parsed)
    events = _events(snapshot)

    halt_count = sum(1 for event in events if event.get("channel") == "halt-scanner")
    changes: list[float] = []
    for event in events:
        value = _number((event.get("data") or {}).get("change_pct"))
        if value is not None:
            changes.append(abs(value))
    max_change = max(changes, default=0.0)

    risk = str(result.get("risk_level") or "MODERATE").upper()
    if halt_count >= 2 or max_change >= 100:
        risk = _minimum_level(risk, "EXTREME", _RISK_ORDER)
    elif halt_count >= 1 or max_change >= 50:
        risk = _minimum_level(risk, "HIGH", _RISK_ORDER)
    result["risk_level"] = risk

    conflicts = [str(value) for value in result.get("data_conflicts") or []]
    conflicts.extend(_detect_local_conflicts(snapshot))
    result["data_conflicts"] = list(dict.fromkeys(conflicts))

    evidence = str(result.get("evidence_quality") or "MEDIUM").upper()
    if result["data_conflicts"]:
        evidence = _maximum_level(evidence, "MEDIUM", _EVIDENCE_ORDER)
    result["evidence_quality"] = evidence

    # Descriptive metadata must not become pseudo-evidence merely because the
    # model echoed it in a positive/risk list.
    positives = [str(value) for value in result.get("positive_factors") or []]
    result["positive_factors"] = [
        value for value in positives if "known runner" not in value.casefold()
    ]

    risks = [str(value) for value in result.get("risk_factors") or []]
    neutral_origin_pattern = re.compile(
        r"(?:china|chinese|country flag|foreign origin|:flag_[a-z]{2}:)",
        re.IGNORECASE,
    )
    result["risk_factors"] = [
        value
        for value in risks
        if not (
            neutral_origin_pattern.search(value)
            and not re.search(r"(?:halt|regulat|delist|listing issue|sanction)", value, re.IGNORECASE)
        )
    ]

    status = str(result.get("review_status") or "WATCH").upper()
    if status == "POTENTIAL SETUP" and (
        risk == "EXTREME" or evidence == "LOW" or bool(result["data_conflicts"])
    ):
        status = "WAIT FOR CONFIRMATION"
    result["review_status"] = status

    return result


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
    parsed = calibrate_review_payload(parsed, snapshot)
    ticker = str(snapshot.get("ticker") or "?").upper()
    return AIReviewResult(
        ticker=ticker,
        model=model,
        interest_level=str(parsed["interest_level"]),
        risk_level=str(parsed["risk_level"]),
        evidence_quality=str(parsed["evidence_quality"]),
        review_status=str(parsed["review_status"]),
        summary=str(parsed["summary"]),
        positive_factors=[str(value) for value in parsed.get("positive_factors") or []],
        risk_factors=[str(value) for value in parsed.get("risk_factors") or []],
        data_conflicts=[str(value) for value in parsed.get("data_conflicts") or []],
        missing_information=[str(value) for value in parsed.get("missing_information") or []],
        next_signals_to_watch=[str(value) for value in parsed.get("next_signals_to_watch") or []],
        invalidation_warnings=[str(value) for value in parsed.get("invalidation_warnings") or []],
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
                    snapshot_json TEXT NOT NULL,
                    review_version INTEGER NOT NULL DEFAULT 1,
                    interest_level TEXT,
                    risk_level TEXT,
                    evidence_quality TEXT,
                    review_status TEXT
                )
                """
            )
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(ai_reviews)")
            }
            migrations = {
                "review_version": "INTEGER NOT NULL DEFAULT 1",
                "interest_level": "TEXT",
                "risk_level": "TEXT",
                "evidence_quality": "TEXT",
                "review_status": "TEXT",
            }
            for column, definition in migrations.items():
                if column not in columns:
                    connection.execute(
                        f"ALTER TABLE ai_reviews ADD COLUMN {column} {definition}"
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
                    result_json, snapshot_json, review_version, interest_level,
                    risk_level, evidence_quality, review_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.ticker,
                    result.created_at,
                    result.model,
                    result.review_status,
                    result.evidence_quality,
                    result.summary,
                    json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True),
                    json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
                    result.review_version,
                    result.interest_level,
                    result.risk_level,
                    result.evidence_quality,
                    result.review_status,
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
