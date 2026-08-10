from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .calibration_stats import CalibrationStatsEngine
from .trade_plan_stats import TradePlanStatsEngine


QUALIFICATION_VERSION = 1
MIN_GLOBAL_RESOLVED = 30
MIN_PATTERN_RESOLVED = 15
MIN_POSITIVE_PATTERNS = 2
POSITIVE_T1_RATE_PCT = 60.0
MAX_POSITIVE_STOP_FIRST_PCT = 30.0
NEGATIVE_T1_RATE_PCT = 30.0
NEGATIVE_STOP_FIRST_PCT = 50.0
NON_SPECIFIC_PATTERNS = {"ALL HIGH ATTENTION", "ALL POTENTIAL TRADE"}


class CandidateQualificationEngine:
    """Experimental evidence gate for HIGH ATTENTION candidates.

    This engine does not place orders and does not generate entry/stop/target
    levels. It only asks whether detection-time conditions have enough resolved
    trade-plan history to deserve the next review stage.
    """

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.calibration = CalibrationStatsEngine(self.database_path)
        self.trade_stats = TradePlanStatsEngine(self.database_path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=3.0)
        connection.row_factory = sqlite3.Row
        return connection

    def latest_session_for_ticker(self, ticker: str) -> dict[str, Any] | None:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT * FROM market_tracking_sessions
                    WHERE UPPER(ticker)=?
                    ORDER BY started_at DESC, id DESC
                    LIMIT 1
                    """,
                    (ticker.upper().strip(),),
                ).fetchone()
        except sqlite3.OperationalError:
            return None
        return dict(row) if row is not None else None

    def refresh(self, limit: int = 500) -> dict[str, int]:
        trade = self.trade_stats.refresh(limit=limit)
        return {
            "evaluation_changes": int(trade.get("evaluation_changes") or 0),
            "feature_changes": int(trade.get("feature_changes") or 0),
        }

    @staticmethod
    def _pattern_classification(row: dict[str, Any]) -> str:
        resolved = int(row.get("resolved_count") or 0)
        if resolved < MIN_PATTERN_RESOLVED:
            return "IMMATURE"
        t1 = row.get("t1_reached_pct")
        stop = row.get("stop_first_pct")
        if t1 is None or stop is None:
            return "IMMATURE"
        t1 = float(t1)
        stop = float(stop)
        if t1 >= POSITIVE_T1_RATE_PCT and stop <= MAX_POSITIVE_STOP_FIRST_PCT:
            return "POSITIVE"
        if t1 <= NEGATIVE_T1_RATE_PCT or stop >= NEGATIVE_STOP_FIRST_PCT:
            return "NEGATIVE"
        return "NEUTRAL"

    def qualify_session(self, session_id: int) -> dict[str, Any]:
        snapshot = self.calibration.ensure_feature_snapshot(int(session_id))
        global_stats = self.trade_stats.overview()
        global_resolved = int(global_stats.get("resolved_count") or 0)

        if snapshot is None:
            return {
                "qualification_version": QUALIFICATION_VERSION,
                "status": "INSUFFICIENT EVIDENCE",
                "reason": "No frozen detection-time feature snapshot exists for this tracking session yet.",
                "global_resolved": global_resolved,
                "positive_patterns": [],
                "negative_patterns": [],
                "neutral_patterns": [],
                "immature_patterns": [],
            }

        tags = [str(tag) for tag in (snapshot.get("tags") or [])]
        pattern_map = self.trade_stats.pattern_map(min_resolved=1)
        matched: list[dict[str, Any]] = []
        for tag in tags:
            row = pattern_map.get(tag)
            if row is None:
                continue
            item = dict(row)
            item["classification"] = self._pattern_classification(item)
            item["specific"] = str(item.get("pattern") or "") not in NON_SPECIFIC_PATTERNS
            matched.append(item)

        positives = [
            row for row in matched
            if row["classification"] == "POSITIVE" and row.get("specific")
        ]
        negatives = [
            row for row in matched
            if row["classification"] == "NEGATIVE" and row.get("specific")
        ]
        neutrals = [
            row for row in matched
            if row["classification"] == "NEUTRAL" and row.get("specific")
        ]
        immature = [
            row for row in matched
            if row["classification"] == "IMMATURE" and row.get("specific")
        ]

        if global_resolved < MIN_GLOBAL_RESOLVED:
            status = "INSUFFICIENT EVIDENCE"
            reason = (
                f"Only {global_resolved} resolved entered trade-plan case(s) exist globally; "
                f"v1 requires at least {MIN_GLOBAL_RESOLVED} before experimental qualification is enabled."
            )
        elif negatives:
            status = "MONITOR / RISK"
            reason = (
                f"{len(negatives)} mature specific matched detection pattern(s) currently show unfavorable trade-plan follow-up. "
                "Do not promote automatically."
            )
        elif len(positives) >= MIN_POSITIVE_PATTERNS:
            status = "EXPERIMENTALLY QUALIFIED"
            reason = (
                f"{len(positives)} mature specific matched detection pattern(s) meet the current conservative trade-plan thresholds "
                "and no mature specific negative pattern matched. This still requires the trade-plan/chart stage."
            )
        elif positives:
            status = "MONITOR"
            reason = (
                f"Only {len(positives)} mature specific positive matched pattern(s) exist; "
                f"v1 requires at least {MIN_POSITIVE_PATTERNS} before experimental qualification."
            )
        else:
            status = "MONITOR"
            reason = "No mature specific matched detection pattern currently provides enough positive trade-plan evidence."

        return {
            "qualification_version": QUALIFICATION_VERSION,
            "status": status,
            "reason": reason,
            "ticker": snapshot.get("ticker"),
            "session_id": int(session_id),
            "trigger_at": snapshot.get("trigger_at"),
            "global_resolved": global_resolved,
            "matched_pattern_count": len(matched),
            "positive_patterns": positives,
            "negative_patterns": negatives,
            "neutral_patterns": neutrals,
            "immature_patterns": immature,
            "thresholds": {
                "minimum_global_resolved": MIN_GLOBAL_RESOLVED,
                "minimum_pattern_resolved": MIN_PATTERN_RESOLVED,
                "minimum_positive_patterns": MIN_POSITIVE_PATTERNS,
                "positive_t1_rate_pct": POSITIVE_T1_RATE_PCT,
                "maximum_positive_stop_first_pct": MAX_POSITIVE_STOP_FIRST_PCT,
                "negative_t1_rate_pct": NEGATIVE_T1_RATE_PCT,
                "negative_stop_first_pct": NEGATIVE_STOP_FIRST_PCT,
            },
        }

    def qualify_ticker(self, ticker: str) -> dict[str, Any]:
        session = self.latest_session_for_ticker(ticker)
        if session is None:
            return {
                "qualification_version": QUALIFICATION_VERSION,
                "ticker": ticker.upper().strip(),
                "status": "INSUFFICIENT EVIDENCE",
                "reason": "No market tracking session exists for this ticker yet.",
                "global_resolved": int(self.trade_stats.overview().get("resolved_count") or 0),
                "positive_patterns": [],
                "negative_patterns": [],
                "neutral_patterns": [],
                "immature_patterns": [],
            }
        return self.qualify_session(int(session["id"]))
