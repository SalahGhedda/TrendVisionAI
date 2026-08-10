from __future__ import annotations

import json
import sqlite3
import statistics
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .automatic_outcomes import (
    SCOPE_MARKET_SESSION,
    AutomaticOutcomeStore,
)


FEATURE_WINDOW_MINUTES = 30
FEATURE_VERSION = 1

UP_LABELS = {"STRONG UP CONTINUATION", "MODEST UP CONTINUATION"}
REVERSAL_LABELS = {"SPIKE THEN REVERSAL"}
NEGATIVE_LABELS = {"STRONG DOWN MOVE", "NEGATIVE OUTCOME"}
VOLATILE_LABELS = {"TWO-SIDED VOLATILITY", "MIXED / RANGE"}


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


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


def _decode_json(value: Any, fallback: Any) -> Any:
    try:
        decoded = json.loads(value or "")
    except (json.JSONDecodeError, TypeError):
        return fallback
    return decoded


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().upper().replace("$", "").replace(",", "")
    if not text:
        return None
    if text.endswith("X") or text.endswith("%"):
        text = text[:-1]
    multiplier = 1.0
    if text.endswith("K"):
        multiplier = 1_000.0
        text = text[:-1]
    elif text.endswith("M"):
        multiplier = 1_000_000.0
        text = text[:-1]
    elif text.endswith("B"):
        multiplier = 1_000_000_000.0
        text = text[:-1]
    elif text.endswith("T"):
        multiplier = 1_000_000_000_000.0
        text = text[:-1]
    try:
        return float(text) * multiplier
    except ValueError:
        return None


def _bucket_rv(value: float | None) -> str:
    if value is None:
        return "RV: UNKNOWN"
    if value < 2:
        return "RV: <2x"
    if value < 5:
        return "RV: 2-5x"
    if value < 10:
        return "RV: 5-10x"
    if value < 20:
        return "RV: 10-20x"
    return "RV: 20x+"


def _bucket_extension(value: float | None) -> str:
    if value is None:
        return "EXTENSION: UNKNOWN"
    if value < 20:
        return "EXTENSION: <20%"
    if value < 50:
        return "EXTENSION: 20-50%"
    if value < 100:
        return "EXTENSION: 50-100%"
    return "EXTENSION: 100%+"


def _bucket_market_cap(value: float | None) -> str:
    if value is None:
        return "MCAP: UNKNOWN"
    if value < 50_000_000:
        return "MCAP: <50M"
    if value < 200_000_000:
        return "MCAP: 50-200M"
    if value < 1_000_000_000:
        return "MCAP: 200M-1B"
    return "MCAP: 1B+"


def _bucket_score(value: int) -> str:
    if value <= 12:
        return "ATTENTION SCORE: 10-12"
    if value <= 15:
        return "ATTENTION SCORE: 13-15"
    return "ATTENTION SCORE: 16+"


def _channel_count_tag(count: int) -> str:
    if count >= 4:
        return "CHANNEL COUNT: 4+"
    return f"CHANNEL COUNT: {max(0, count)}"


def _evidence_label(samples: int) -> str:
    if samples < 5:
        return "TOO EARLY"
    if samples < 15:
        return "EARLY"
    if samples < 30:
        return "BUILDING"
    return "MORE STABLE"


class CalibrationStatsEngine:
    """Compare detection-time TrendVision conditions with later market outcomes.

    Feature snapshots are frozen around the HIGH ATTENTION trigger so later
    scanner events cannot leak into the detection-time feature set. Statistics
    are descriptive observational summaries, not trading rules.
    """

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.outcomes = AutomaticOutcomeStore(self.database_path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=3.0)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS calibration_feature_snapshots (
                    session_id INTEGER PRIMARY KEY,
                    ticker TEXT NOT NULL,
                    trigger_at TEXT NOT NULL,
                    feature_window_minutes INTEGER NOT NULL,
                    feature_version INTEGER NOT NULL,
                    built_at TEXT NOT NULL,
                    features_json TEXT NOT NULL DEFAULT '{}',
                    tags_json TEXT NOT NULL DEFAULT '[]'
                );
                CREATE INDEX IF NOT EXISTS idx_calibration_feature_ticker
                    ON calibration_feature_snapshots(ticker, trigger_at DESC);
                """
            )

    def _session(self, session_id: int) -> dict[str, Any] | None:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM market_tracking_sessions WHERE id=?",
                    (int(session_id),),
                ).fetchone()
        except sqlite3.OperationalError:
            return None
        return dict(row) if row is not None else None

    def _events_before_trigger(
        self,
        *,
        ticker: str,
        trigger_at: str,
        window_minutes: int,
    ) -> list[dict[str, Any]]:
        trigger_time = _parse_time(trigger_at)
        if trigger_time is None:
            return []
        start_time = trigger_time - timedelta(minutes=max(1, int(window_minutes)))
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT id, received_at, channel, event_type, headline, data_json
                    FROM scanner_events
                    WHERE UPPER(ticker)=?
                    ORDER BY received_at ASC, id ASC
                    """,
                    (ticker.upper().strip(),),
                ).fetchall()
        except sqlite3.OperationalError:
            return []

        events: list[dict[str, Any]] = []
        for row in rows:
            event_time = _parse_time(row["received_at"])
            if event_time is None:
                continue
            if _seconds_between(event_time, start_time) < 0:
                continue
            if _seconds_between(trigger_time, event_time) < 0:
                continue
            data = _decode_json(row["data_json"], {})
            events.append(
                {
                    "id": int(row["id"]),
                    "received_at": row["received_at"],
                    "channel": str(row["channel"] or ""),
                    "event_type": str(row["event_type"] or ""),
                    "headline": str(row["headline"] or ""),
                    "data": data if isinstance(data, dict) else {},
                }
            )
        return events

    def _build_features(self, session: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        ticker = str(session.get("ticker") or "").upper().strip()
        trigger_at = str(session.get("started_at") or "")
        events = self._events_before_trigger(
            ticker=ticker,
            trigger_at=trigger_at,
            window_minutes=FEATURE_WINDOW_MINUTES,
        )

        channels: list[str] = []
        signals: list[str] = []
        rv_values: list[float] = []
        change_values: list[float] = []
        market_cap_latest: float | None = None
        zero_borrow = False
        whale_up = False
        whale_down = False
        pre_trigger_halts = 0

        for event in events:
            channel = str(event.get("channel") or "")
            if channel and channel not in channels:
                channels.append(channel)
            data = event.get("data") or {}

            signal = str(data.get("signal") or "").upper().strip()
            if signal and signal not in signals:
                signals.append(signal)

            rv = _number(data.get("relative_volume"))
            if rv is not None:
                rv_values.append(rv)

            change = _number(data.get("change_pct"))
            if change is not None:
                change_values.append(change)

            market_cap = _number(data.get("market_cap"))
            if market_cap is None:
                market_cap = _number(data.get("market_cap_value"))
            if market_cap is not None:
                market_cap_latest = market_cap

            if data.get("zero_borrow") is True or data.get("no_shares_available") is True:
                zero_borrow = True

            direction = str(data.get("direction") or "").casefold().strip()
            if channel == "whale-scanner" and direction == "up":
                whale_up = True
            elif channel == "whale-scanner" and direction == "down":
                whale_down = True

            if channel == "halt-scanner" or data.get("halt_status"):
                pre_trigger_halts += 1

        relative_volume_max = max(rv_values) if rv_values else None
        max_positive_change = max(change_values) if change_values else None
        trigger_score = int(session.get("trigger_score") or 0)

        features = {
            "ticker": ticker,
            "trigger_at": trigger_at,
            "feature_window_minutes": FEATURE_WINDOW_MINUTES,
            "trigger_tier": session.get("trigger_tier"),
            "trigger_score": trigger_score,
            "event_count": len(events),
            "channel_count": len(channels),
            "channels": channels,
            "signals": signals,
            "relative_volume_max": relative_volume_max,
            "max_change_pct": max_positive_change,
            "market_cap_latest": market_cap_latest,
            "zero_borrow_observed": zero_borrow,
            "whale_up_observed": whale_up,
            "whale_down_observed": whale_down,
            "pre_trigger_halt_count": pre_trigger_halts,
        }

        tags: list[str] = [
            "ALL HIGH ATTENTION",
            _bucket_score(trigger_score),
            _channel_count_tag(len(channels)),
            _bucket_rv(relative_volume_max),
            _bucket_extension(max_positive_change),
            _bucket_market_cap(market_cap_latest),
            (
                "PRE-TRIGGER HALT: OBSERVED"
                if pre_trigger_halts
                else "PRE-TRIGGER HALT: NONE OBSERVED"
            ),
        ]

        for channel in channels:
            tags.append(f"HAS CHANNEL: {channel}")
        if len(channels) >= 2:
            tags.append("CHANNEL COMBO: " + " + ".join(sorted(channels)))
        for signal in signals:
            tags.append(f"SIGNAL: {signal}")
        if zero_borrow:
            tags.append("ZERO BORROW: OBSERVED")
        if whale_up:
            tags.append("WHALE: UP")
        if whale_down:
            tags.append("WHALE: DOWN")

        channel_count = len(channels)
        rv10 = relative_volume_max is not None and relative_volume_max >= 10
        extended100 = max_positive_change is not None and max_positive_change >= 100
        signal_set = set(signals)
        if channel_count >= 3 and rv10:
            tags.append("COMPOUND: 3+ CHANNELS + RV>=10x")
        if "BREAKOUT" in signal_set and rv10:
            tags.append("COMPOUND: BREAKOUT + RV>=10x")
        if "MOMENTUM" in signal_set and rv10:
            tags.append("COMPOUND: MOMENTUM + RV>=10x")
        if "BREAKOUT" in signal_set and channel_count >= 3:
            tags.append("COMPOUND: BREAKOUT + 3+ CHANNELS")
        if "MOMENTUM" in signal_set and channel_count >= 3:
            tags.append("COMPOUND: MOMENTUM + 3+ CHANNELS")
        if extended100 and pre_trigger_halts:
            tags.append("COMPOUND: 100%+ EXTENDED + PRE-TRIGGER HALT")
        if extended100 and channel_count >= 3:
            tags.append("COMPOUND: 100%+ EXTENDED + 3+ CHANNELS")
        if zero_borrow and channel_count >= 3:
            tags.append("COMPOUND: ZERO BORROW + 3+ CHANNELS")

        tags = list(dict.fromkeys(tags))
        return features, tags

    def ensure_feature_snapshot(self, session_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM calibration_feature_snapshots WHERE session_id=?",
                (int(session_id),),
            ).fetchone()
        if row is not None:
            result = dict(row)
            result["features"] = _decode_json(result.get("features_json"), {})
            result["tags"] = _decode_json(result.get("tags_json"), [])
            return result

        session = self._session(session_id)
        if session is None:
            return None
        features, tags = self._build_features(session)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO calibration_feature_snapshots (
                    session_id, ticker, trigger_at, feature_window_minutes,
                    feature_version, built_at, features_json, tags_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(session_id),
                    str(session.get("ticker") or "").upper().strip(),
                    str(session.get("started_at") or ""),
                    FEATURE_WINDOW_MINUTES,
                    FEATURE_VERSION,
                    _now_iso(),
                    json.dumps(features, ensure_ascii=False, sort_keys=True),
                    json.dumps(tags, ensure_ascii=False),
                ),
            )
        return self.ensure_feature_snapshot(session_id)

    def ensure_feature_snapshots(self, limit: int = 500) -> int:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT id FROM market_tracking_sessions
                    ORDER BY started_at DESC, id DESC
                    LIMIT ?
                    """,
                    (max(1, int(limit)),),
                ).fetchall()
        except sqlite3.OperationalError:
            return 0

        created = 0
        for row in rows:
            session_id = int(row["id"])
            with self._connect() as connection:
                exists = connection.execute(
                    "SELECT 1 FROM calibration_feature_snapshots WHERE session_id=?",
                    (session_id,),
                ).fetchone()
            if exists is not None:
                continue
            if self.ensure_feature_snapshot(session_id) is not None:
                created += 1
        return created

    def _snapshot_for_session(self, session_id: int) -> dict[str, Any] | None:
        snapshot = self.ensure_feature_snapshot(session_id)
        if snapshot is None:
            return None
        return snapshot

    def refresh(self, limit: int = 500) -> dict[str, int]:
        outcome_changes = self.outcomes.refresh_all_due_outcomes(limit=limit)
        feature_changes = self.ensure_feature_snapshots(limit=limit)
        return {
            "outcome_changes": int(outcome_changes.get("session_changes") or 0)
            + int(outcome_changes.get("review_changes") or 0),
            "feature_changes": feature_changes,
        }

    def _outcome_rows(self, horizon_minutes: int) -> list[dict[str, Any]]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT subject_id, ticker, label, confidence, flags_json, metrics_json
                    FROM automatic_outcomes
                    WHERE scope=? AND horizon_minutes=?
                    ORDER BY classified_at DESC
                    """,
                    (SCOPE_MARKET_SESSION, int(horizon_minutes)),
                ).fetchall()
        except sqlite3.OperationalError:
            return []
        result: list[dict[str, Any]] = []
        for row in rows:
            metrics = _decode_json(row["metrics_json"], {})
            flags = _decode_json(row["flags_json"], [])
            result.append(
                {
                    "session_id": int(row["subject_id"]),
                    "ticker": row["ticker"],
                    "label": row["label"],
                    "confidence": row["confidence"],
                    "flags": flags if isinstance(flags, list) else [],
                    "metrics": metrics if isinstance(metrics, dict) else {},
                }
            )
        return result

    def overview(self, horizon_minutes: int) -> dict[str, Any]:
        rows = self._outcome_rows(horizon_minutes)
        usable = [row for row in rows if row["label"] != "INSUFFICIENT DATA"]
        up = sum(1 for row in usable if row["label"] in UP_LABELS)
        reversal = sum(1 for row in usable if row["label"] in REVERSAL_LABELS)
        negative = sum(1 for row in usable if row["label"] in NEGATIVE_LABELS)
        return {
            "classified": len(rows),
            "usable": len(usable),
            "insufficient": len(rows) - len(usable),
            "up_rate_pct": (up / len(usable) * 100.0) if usable else None,
            "reversal_rate_pct": (reversal / len(usable) * 100.0) if usable else None,
            "negative_rate_pct": (negative / len(usable) * 100.0) if usable else None,
        }

    def pattern_stats(
        self,
        *,
        horizon_minutes: int,
        min_samples: int = 1,
    ) -> list[dict[str, Any]]:
        groups: dict[str, list[dict[str, Any]]] = {}
        for outcome in self._outcome_rows(horizon_minutes):
            if outcome["label"] == "INSUFFICIENT DATA":
                continue
            snapshot = self._snapshot_for_session(outcome["session_id"])
            if snapshot is None:
                continue
            tags = snapshot.get("tags") or []
            if not isinstance(tags, list):
                continue
            for tag in tags:
                groups.setdefault(str(tag), []).append(outcome)

        result: list[dict[str, Any]] = []
        minimum = max(1, int(min_samples))
        for pattern, rows in groups.items():
            if len(rows) < minimum:
                continue
            returns = [
                _number(row["metrics"].get("return_pct"))
                for row in rows
                if _number(row["metrics"].get("return_pct")) is not None
            ]
            mfes = [
                _number(row["metrics"].get("mfe_pct"))
                for row in rows
                if _number(row["metrics"].get("mfe_pct")) is not None
            ]
            maes = [
                _number(row["metrics"].get("mae_pct"))
                for row in rows
                if _number(row["metrics"].get("mae_pct")) is not None
            ]
            labels: dict[str, int] = {}
            for row in rows:
                labels[row["label"]] = labels.get(row["label"], 0) + 1
            sample_count = len(rows)
            up_count = sum(count for label, count in labels.items() if label in UP_LABELS)
            reversal_count = sum(
                count for label, count in labels.items() if label in REVERSAL_LABELS
            )
            negative_count = sum(
                count for label, count in labels.items() if label in NEGATIVE_LABELS
            )
            volatile_count = sum(
                count for label, count in labels.items() if label in VOLATILE_LABELS
            )
            result.append(
                {
                    "pattern": pattern,
                    "sample_count": sample_count,
                    "median_return_pct": statistics.median(returns) if returns else None,
                    "median_mfe_pct": statistics.median(mfes) if mfes else None,
                    "median_mae_pct": statistics.median(maes) if maes else None,
                    "up_continuation_pct": up_count / sample_count * 100.0,
                    "spike_reversal_pct": reversal_count / sample_count * 100.0,
                    "negative_pct": negative_count / sample_count * 100.0,
                    "volatile_mixed_pct": volatile_count / sample_count * 100.0,
                    "evidence": _evidence_label(sample_count),
                    "label_counts": labels,
                }
            )

        result.sort(
            key=lambda item: (
                -int(item["sample_count"]),
                str(item["pattern"]),
            )
        )
        return result
