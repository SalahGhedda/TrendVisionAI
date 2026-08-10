from __future__ import annotations

import json
import sqlite3
import statistics
from pathlib import Path
from typing import Any

from .calibration_stats import CalibrationStatsEngine
from .trade_plans import TradePlanStore


T1_REACHED_STATUSES = {
    "TARGET 1 HIT / OPEN",
    "TARGET 1 ONLY",
    "TARGET 1 THEN STOP",
    "TARGET 2 HIT",
}
T2_REACHED_STATUSES = {"TARGET 2 HIT"}
STOP_FIRST_STATUSES = {"STOP HIT FIRST"}
COMPLETED_NO_TARGET_STATUSES = {"NO TARGET / NO STOP"}
OPEN_UNRESOLVED_STATUSES = {"OPEN / IN PROGRESS"}


def _decode_json(value: Any, fallback: Any) -> Any:
    try:
        decoded = json.loads(value or "")
    except (json.JSONDecodeError, TypeError):
        return fallback
    return decoded


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def evidence_label(samples: int) -> str:
    if samples < 5:
        return "TOO EARLY"
    if samples < 15:
        return "EARLY"
    if samples < 30:
        return "BUILDING"
    return "MORE STABLE"


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _rate(numerator: int, denominator: int) -> float | None:
    return (numerator / denominator * 100.0) if denominator else None


def _setup_family_tags(setup_type: str) -> list[str]:
    low = str(setup_type or "").casefold()
    tags: list[str] = []
    if "breakout" in low:
        tags.append("PLAN SETUP: BREAKOUT")
    if "pullback" in low or "retest" in low:
        tags.append("PLAN SETUP: PULLBACK / RETEST")
    if "momentum" in low:
        tags.append("PLAN SETUP: MOMENTUM")
    if "reversal" in low:
        tags.append("PLAN SETUP: REVERSAL")
    if "gap" in low or "runner" in low:
        tags.append("PLAN SETUP: GAP / RUNNER")
    return tags


class TradePlanStatsEngine:
    """Aggregate saved experimental trade plans against their objective follow-up.

    The engine intentionally separates actionable plans, entry observations and
    resolved post-entry cases. T1/T2/stop rates are descriptive calibration
    statistics; they are not expected-return or profitability claims.
    """

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.store = TradePlanStore(self.database_path)
        self.calibration = CalibrationStatsEngine(self.database_path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=3.0)
        connection.row_factory = sqlite3.Row
        return connection

    def refresh(self, limit: int = 500) -> dict[str, int]:
        evaluation_changes = self.store.refresh_evaluations(limit=limit)
        feature_changes = self.calibration.ensure_feature_snapshots(limit=limit)
        return {
            "evaluation_changes": int(evaluation_changes or 0),
            "feature_changes": int(feature_changes or 0),
        }

    def _rows(self, limit: int = 1000) -> list[dict[str, Any]]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT
                        p.id, p.ticker, p.created_at, p.plan_version, p.decision,
                        p.confidence, p.risk_level, p.chart_structure, p.setup_type,
                        p.entry_low, p.entry_high, p.stop_loss, p.target_1, p.target_2,
                        p.result_json, p.snapshot_json,
                        e.status AS evaluation_status,
                        e.horizon_complete,
                        e.entry_reached_at,
                        e.entry_price,
                        e.target_1_hit_at,
                        e.target_2_hit_at,
                        e.stop_hit_at,
                        e.max_return_pct,
                        e.max_drawdown_pct,
                        e.sample_count AS evaluation_sample_count,
                        e.evaluation_json
                    FROM trade_plans p
                    LEFT JOIN trade_plan_evaluations e ON e.plan_id = p.id
                    ORDER BY p.created_at DESC, p.id DESC
                    LIMIT ?
                    """,
                    (max(1, int(limit)),),
                ).fetchall()
        except sqlite3.OperationalError:
            return []

        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["result"] = _decode_json(item.get("result_json"), {})
            item["snapshot"] = _decode_json(item.get("snapshot_json"), {})
            item["evaluation"] = _decode_json(item.get("evaluation_json"), {})
            result.append(item)
        return result

    @staticmethod
    def _is_actionable(row: dict[str, Any]) -> bool:
        if str(row.get("decision") or "") != "POTENTIAL TRADE":
            return False
        return all(
            _num(row.get(key)) is not None
            for key in ("entry_low", "entry_high", "stop_loss", "target_1", "target_2")
        )

    @staticmethod
    def _is_entered(row: dict[str, Any]) -> bool:
        return bool(row.get("entry_reached_at"))

    @staticmethod
    def _is_resolved(row: dict[str, Any]) -> bool:
        status = str(row.get("evaluation_status") or "")
        return status in (
            T1_REACHED_STATUSES
            | STOP_FIRST_STATUSES
            | COMPLETED_NO_TARGET_STATUSES
        )

    def _detection_tags(self, row: dict[str, Any]) -> list[str]:
        snapshot = row.get("snapshot") or {}
        market = snapshot.get("alpaca_market_context") or {}
        session_id = market.get("session_id")
        if session_id is None:
            return []
        try:
            feature_snapshot = self.calibration.ensure_feature_snapshot(int(session_id))
        except (TypeError, ValueError):
            return []
        if not feature_snapshot:
            return []
        tags = feature_snapshot.get("tags") or []
        return [str(tag) for tag in tags] if isinstance(tags, list) else []

    def _plan_tags(self, row: dict[str, Any]) -> list[str]:
        tags = ["ALL POTENTIAL TRADE"]
        chart = str(row.get("chart_structure") or "").upper().strip()
        risk = str(row.get("risk_level") or "").upper().strip()
        confidence = str(row.get("confidence") or "").upper().strip()
        if chart:
            tags.append(f"PLAN CHART: {chart}")
        if risk:
            tags.append(f"PLAN RISK: {risk}")
        if confidence:
            tags.append(f"PLAN CONFIDENCE: {confidence}")
        tags.extend(_setup_family_tags(str(row.get("setup_type") or "")))
        return tags

    def tags_for_plan(self, row: dict[str, Any]) -> list[str]:
        if not self._is_actionable(row):
            return []
        return list(dict.fromkeys([*self._detection_tags(row), *self._plan_tags(row)]))

    @staticmethod
    def _summarize(rows: list[dict[str, Any]], pattern: str) -> dict[str, Any]:
        actionable = len(rows)
        entered_rows = [row for row in rows if TradePlanStatsEngine._is_entered(row)]
        resolved_rows = [row for row in entered_rows if TradePlanStatsEngine._is_resolved(row)]

        t1_count = sum(
            1 for row in resolved_rows
            if str(row.get("evaluation_status") or "") in T1_REACHED_STATUSES
        )
        t2_count = sum(
            1 for row in resolved_rows
            if str(row.get("evaluation_status") or "") in T2_REACHED_STATUSES
        )
        stop_first_count = sum(
            1 for row in resolved_rows
            if str(row.get("evaluation_status") or "") in STOP_FIRST_STATUSES
        )
        no_target_count = sum(
            1 for row in resolved_rows
            if str(row.get("evaluation_status") or "") in COMPLETED_NO_TARGET_STATUSES
        )
        unresolved_entered = sum(
            1 for row in entered_rows
            if str(row.get("evaluation_status") or "") in OPEN_UNRESOLVED_STATUSES
        )

        entry_opportunity_rows = [
            row for row in rows
            if bool(row.get("horizon_complete")) or TradePlanStatsEngine._is_entered(row)
        ]
        returns = [
            value for value in (_num(row.get("max_return_pct")) for row in entered_rows)
            if value is not None
        ]
        drawdowns = [
            value for value in (_num(row.get("max_drawdown_pct")) for row in entered_rows)
            if value is not None
        ]
        result_values = [row.get("result") or {} for row in rows]
        rr1 = [
            value for value in (_num(result.get("risk_reward_target_1")) for result in result_values)
            if value is not None
        ]
        rr2 = [
            value for value in (_num(result.get("risk_reward_target_2")) for result in result_values)
            if value is not None
        ]

        resolved_count = len(resolved_rows)
        return {
            "pattern": pattern,
            "actionable_count": actionable,
            "entry_opportunity_count": len(entry_opportunity_rows),
            "entered_count": len(entered_rows),
            "resolved_count": resolved_count,
            "unresolved_entered_count": unresolved_entered,
            "t1_reached_count": t1_count,
            "t2_reached_count": t2_count,
            "stop_first_count": stop_first_count,
            "no_target_no_stop_count": no_target_count,
            "entry_reached_pct": _rate(len(entered_rows), len(entry_opportunity_rows)),
            "t1_reached_pct": _rate(t1_count, resolved_count),
            "t2_reached_pct": _rate(t2_count, resolved_count),
            "stop_first_pct": _rate(stop_first_count, resolved_count),
            "no_target_no_stop_pct": _rate(no_target_count, resolved_count),
            "median_max_return_pct": _median(returns),
            "median_max_drawdown_pct": _median(drawdowns),
            "median_planned_rr_t1": _median(rr1),
            "median_planned_rr_t2": _median(rr2),
            "evidence": evidence_label(resolved_count),
        }

    def overview(self) -> dict[str, Any]:
        rows = self._rows()
        potential = [row for row in rows if self._is_actionable(row)]
        summary = self._summarize(potential, "ALL POTENTIAL TRADE")
        summary.update(
            {
                "total_plans": len(rows),
                "potential_plans": len(potential),
                "watch_plans": sum(1 for row in rows if row.get("decision") == "WATCH"),
                "rejected_plans": sum(1 for row in rows if row.get("decision") == "REJECT"),
            }
        )
        return summary

    def pattern_stats(self, *, min_resolved: int = 1) -> list[dict[str, Any]]:
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in self._rows():
            if not self._is_actionable(row):
                continue
            for tag in self.tags_for_plan(row):
                groups.setdefault(tag, []).append(row)

        minimum = max(0, int(min_resolved))
        result: list[dict[str, Any]] = []
        for pattern, rows in groups.items():
            summary = self._summarize(rows, pattern)
            if int(summary.get("resolved_count") or 0) < minimum:
                continue
            result.append(summary)

        result.sort(
            key=lambda row: (
                int(row.get("resolved_count") or 0),
                int(row.get("entered_count") or 0),
                int(row.get("actionable_count") or 0),
                str(row.get("pattern") or ""),
            ),
            reverse=True,
        )
        return result

    def pattern_map(self, *, min_resolved: int = 1) -> dict[str, dict[str, Any]]:
        return {
            str(row["pattern"]): row
            for row in self.pattern_stats(min_resolved=min_resolved)
        }
