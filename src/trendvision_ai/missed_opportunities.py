from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from .live_pipeline import LivePipelineStore
from .market_data import MarketDataStore


DEFAULT_MAJOR_MOVE_PCT = 8.0


class MissedOpportunityAnalyzer:
    """Explain what the pipeline did after a HIGH ATTENTION tracking session.

    This is a hindsight diagnostics tool, not a claim that every later price run
    was safely tradable. It answers where the pipeline stopped: no setup, Terra
    WATCH/REJECT, hard gate, or a final alert.
    """

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.market = MarketDataStore(database_path)
        self.live = LivePipelineStore(database_path)

    def recent_rows(
        self,
        *,
        limit: int = 40,
        major_move_pct: float = DEFAULT_MAJOR_MOVE_PCT,
    ) -> list[dict[str, Any]]:
        sessions = self.market.list_sessions(limit=max(1, int(limit)))
        events = self.live.list_events(limit=max(1000, int(limit) * 40))
        by_session: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for event in events:
            try:
                session_id = int(event.get("session_id"))
            except (TypeError, ValueError):
                continue
            by_session[session_id].append(event)

        rows: list[dict[str, Any]] = []
        threshold = float(major_move_pct)
        for session in sessions:
            session_id = int(session["id"])
            metrics = self.market.session_metrics(session_id)
            session_events = by_session.get(session_id, [])
            strategy_events = [row for row in session_events if row.get("stage") == "STRATEGY_MATCH"]
            plan_events = [row for row in session_events if row.get("stage") == "AUTO_PLAN"]
            blocked_events = [row for row in session_events if row.get("stage") == "ALERT_BLOCKED"]
            alert_events = [row for row in session_events if row.get("stage") == "FINAL_TRADE_ALERT"]
            scan_events = [row for row in session_events if row.get("stage") == "STRATEGY_SCAN"]

            decisions = [str(row.get("status") or "").upper() for row in plan_events]
            blockers: list[str] = []
            for event in blocked_events:
                payload = event.get("payload") or {}
                blockers.extend(str(value) for value in (payload.get("blockers") or []))
            blockers = list(dict.fromkeys(value for value in blockers if value))

            mfe = metrics.get("mfe_pct")
            try:
                mfe_value = float(mfe) if mfe is not None else None
            except (TypeError, ValueError):
                mfe_value = None
            major_move = bool(mfe_value is not None and mfe_value >= threshold)

            if alert_events:
                diagnosis = "ALERTED"
                reason = "A FINAL_TRADE_ALERT was produced for this tracking session."
            elif not major_move:
                diagnosis = "NO MAJOR MOVE"
                reason = f"Observed MFE did not reach the {threshold:.1f}% diagnostics threshold."
            elif not strategy_events:
                diagnosis = "MISSED — NO SETUP"
                if scan_events:
                    reason = "The stock later made a major move, but no configured setup instance was recognized during scans."
                else:
                    reason = "The stock later made a major move, but no strategy-recognition event was recorded for this session."
            elif not plan_events:
                diagnosis = "MISSED — PLAN NOT RUN"
                reason = "A setup was recognized, but no automatic Terra Trade Plan completed for the session."
            elif blocked_events:
                diagnosis = "MISSED — HARD GATE"
                reason = "Terra produced a potential plan, but the deterministic final gate blocked the alert"
                if blockers:
                    reason += f": {', '.join(blockers)}."
                else:
                    reason += "."
            elif "WATCH" in decisions:
                diagnosis = "MISSED — TERRA WATCH"
                reason = "At least one recognized setup reached Terra, but the completed Trade Plan stayed WATCH."
            elif "REJECT" in decisions:
                diagnosis = "MISSED — TERRA REJECT"
                reason = "At least one recognized setup reached Terra, but the completed Trade Plan was REJECT."
            elif "POTENTIAL TRADE" in decisions:
                diagnosis = "MISSED — NO FINAL ALERT"
                reason = "A POTENTIAL TRADE was recorded without a matching final alert; inspect the live pipeline for the exact blocker/state."
            else:
                diagnosis = "MISSED — OTHER"
                reason = "The stock later made a major move, but the recorded pipeline state does not fit a common diagnostic bucket."

            rows.append(
                {
                    "session_id": session_id,
                    "started_at": session.get("started_at"),
                    "ticker": str(session.get("ticker") or "").upper(),
                    "trigger_score": session.get("trigger_score"),
                    "reference_price": session.get("reference_price"),
                    "mfe_pct": mfe_value,
                    "mae_pct": metrics.get("mae_pct"),
                    "time_to_peak_minutes": metrics.get("time_to_peak_minutes"),
                    "sample_count": metrics.get("sample_count"),
                    "strategy_matches": len(strategy_events),
                    "terra_plans": len(plan_events),
                    "decisions": decisions,
                    "blocked_count": len(blocked_events),
                    "final_alerts": len(alert_events),
                    "major_move": major_move,
                    "diagnosis": diagnosis,
                    "reason": reason,
                    "blockers": blockers,
                }
            )

        rows.sort(
            key=lambda row: (
                0 if row.get("major_move") and not row.get("final_alerts") else 1,
                -(float(row.get("mfe_pct") or 0.0)),
                str(row.get("started_at") or ""),
            )
        )
        return rows

    @staticmethod
    def stats(rows: list[dict[str, Any]]) -> dict[str, int]:
        major = [row for row in rows if row.get("major_move")]
        missed = [row for row in major if not row.get("final_alerts")]
        return {
            "major_runners": len(major),
            "alerted": sum(1 for row in major if row.get("final_alerts")),
            "missed": len(missed),
            "no_setup": sum(1 for row in missed if row.get("diagnosis") == "MISSED — NO SETUP"),
            "terra_filtered": sum(
                1
                for row in missed
                if row.get("diagnosis") in {"MISSED — TERRA WATCH", "MISSED — TERRA REJECT"}
            ),
            "hard_gate": sum(1 for row in missed if row.get("diagnosis") == "MISSED — HARD GATE"),
        }
