from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from .live_pipeline import LivePipelineStore
from .market_data import MarketDataStore


DEFAULT_MAJOR_MOVE_PCT = 8.0
DIAGNOSTIC_HORIZON_MINUTES = 240


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _event_sort_key(event: dict[str, Any]) -> tuple[str, int]:
    return str(event.get("created_at") or ""), int(event.get("id") or 0)


class MissedOpportunityAnalyzer:
    """Explain what happened after each stage of a HIGH ATTENTION session.

    The important distinction is timing. A stock can be +30% after HIGH ATTENTION
    but have already completed that move before a setup or Terra decision existed.
    We therefore measure MFE independently after HIGH ATTENTION, first setup
    recognition and first completed Terra plan.
    """

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.market = MarketDataStore(database_path)
        self.live = LivePipelineStore(database_path)

    def _metrics_after_event(self, ticker: str, event: dict[str, Any] | None) -> dict[str, Any]:
        if not event or not event.get("created_at"):
            return {}
        try:
            return self.market.review_metrics(
                ticker=ticker,
                review_created_at=str(event["created_at"]),
                horizon_minutes=DIAGNOSTIC_HORIZON_MINUTES,
            )
        except Exception:
            return {}

    def recent_rows(
        self,
        *,
        limit: int = 40,
        major_move_pct: float = DEFAULT_MAJOR_MOVE_PCT,
    ) -> list[dict[str, Any]]:
        sessions = self.market.list_sessions(limit=max(1, int(limit)))
        events = self.live.list_events(limit=max(1200, int(limit) * 60))
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
            ticker = str(session.get("ticker") or "").upper()
            metrics = self.market.session_metrics(session_id)
            session_events = sorted(by_session.get(session_id, []), key=_event_sort_key)
            strategy_events = [row for row in session_events if row.get("stage") == "STRATEGY_MATCH"]
            plan_events = [row for row in session_events if row.get("stage") == "AUTO_PLAN"]
            blocked_events = [row for row in session_events if row.get("stage") == "ALERT_BLOCKED"]
            alert_events = [row for row in session_events if row.get("stage") == "FINAL_TRADE_ALERT"]
            scan_events = [row for row in session_events if row.get("stage") == "STRATEGY_SCAN"]

            first_strategy = strategy_events[0] if strategy_events else None
            first_plan = plan_events[0] if plan_events else None
            after_strategy = self._metrics_after_event(ticker, first_strategy)
            after_terra = self._metrics_after_event(ticker, first_plan)

            decisions = [str(row.get("status") or "").upper() for row in plan_events]
            first_terra_decision = str((first_plan or {}).get("status") or "").upper() or None
            blockers: list[str] = []
            for event in blocked_events:
                payload = event.get("payload") or {}
                blockers.extend(str(value) for value in (payload.get("blockers") or []))
            blockers = list(dict.fromkeys(value for value in blockers if value))

            attention_mfe = _num(metrics.get("mfe_pct"))
            setup_mfe = _num(after_strategy.get("mfe_pct"))
            terra_mfe = _num(after_terra.get("mfe_pct"))
            major_move = bool(attention_mfe is not None and attention_mfe >= threshold)
            major_after_setup = bool(setup_mfe is not None and setup_mfe >= threshold)
            major_after_terra = bool(terra_mfe is not None and terra_mfe >= threshold)

            if alert_events:
                diagnosis = "ALERTED"
                reason = "A FINAL_TRADE_ALERT was produced for this tracking session."
            elif not major_move:
                diagnosis = "NO MAJOR MOVE"
                reason = f"MFE after HIGH ATTENTION did not reach the {threshold:.1f}% diagnostics threshold."
            elif not strategy_events:
                diagnosis = "MISSED — NO SETUP"
                if scan_events:
                    reason = "A major move followed HIGH ATTENTION, but no configured setup instance was recognized during scans."
                else:
                    reason = "A major move followed HIGH ATTENTION, but no strategy-recognition event was recorded for this session."
            elif setup_mfe is not None and not major_after_setup:
                diagnosis = "LATE — MOVE BEFORE SETUP"
                reason = (
                    f"The stock moved {attention_mfe:.1f}%+ after HIGH ATTENTION, but only {setup_mfe:.1f}% MFE remained after the first setup was recognized. "
                    "This points more to late setup recognition than to a Terra rejection."
                )
            elif not plan_events:
                diagnosis = "MISSED — PLAN NOT RUN"
                reason = "A setup was recognized while a major continuation remained, but no automatic Terra Trade Plan completed."
            elif terra_mfe is not None and not major_after_terra and first_terra_decision in {"WATCH", "REJECT"}:
                diagnosis = "FILTERED — NO MAJOR MOVE AFTER TERRA"
                reason = (
                    f"The stock had already made its major move earlier; after Terra returned {first_terra_decision}, observed MFE was only {terra_mfe:.1f}%. "
                    "This is not counted as a Terra missed runner."
                )
            elif blocked_events and major_after_terra:
                diagnosis = "MISSED — HARD GATE"
                reason = "A major continuation remained after the first Terra decision, but a later potential plan was blocked by the deterministic final gate"
                if blockers:
                    reason += f": {', '.join(blockers)}."
                else:
                    reason += "."
            elif first_terra_decision == "WATCH" and major_after_terra:
                diagnosis = "MISSED — TERRA WATCH"
                reason = (
                    f"Terra returned WATCH while at least {terra_mfe:.1f}% MFE was still observed afterward. "
                    "This is a genuine candidate for reviewing whether Terra was too conservative or the move lacked a safe entry."
                )
            elif first_terra_decision == "REJECT" and major_after_terra:
                diagnosis = "MISSED — TERRA REJECT"
                reason = (
                    f"Terra returned REJECT while at least {terra_mfe:.1f}% MFE was still observed afterward. "
                    "Review the saved Terra response and chart before changing the prompt."
                )
            elif "POTENTIAL TRADE" in decisions and major_after_terra:
                diagnosis = "MISSED — NO FINAL ALERT"
                reason = "A major continuation remained after Terra and a POTENTIAL TRADE was recorded without a matching final alert; inspect the live pipeline for the exact state."
            elif terra_mfe is not None and not major_after_terra:
                diagnosis = "FILTERED — NO MAJOR MOVE AFTER TERRA"
                reason = f"Less than {threshold:.1f}% MFE remained after the first completed Terra plan, so the earlier HIGH ATTENTION run is not attributed to the final decision layer."
            else:
                diagnosis = "UNRESOLVED — NEED MORE DATA"
                reason = "The session was a major runner from HIGH ATTENTION, but there is not yet enough post-stage market data to assign the miss fairly."

            rows.append(
                {
                    "session_id": session_id,
                    "started_at": session.get("started_at"),
                    "ticker": ticker,
                    "trigger_score": session.get("trigger_score"),
                    "reference_price": session.get("reference_price"),
                    "mfe_pct": attention_mfe,
                    "mae_pct": metrics.get("mae_pct"),
                    "time_to_peak_minutes": metrics.get("time_to_peak_minutes"),
                    "sample_count": metrics.get("sample_count"),
                    "first_strategy_at": (first_strategy or {}).get("created_at"),
                    "mfe_after_strategy_pct": setup_mfe,
                    "mae_after_strategy_pct": after_strategy.get("mae_pct"),
                    "first_terra_at": (first_plan or {}).get("created_at"),
                    "first_terra_decision": first_terra_decision,
                    "mfe_after_terra_pct": terra_mfe,
                    "mae_after_terra_pct": after_terra.get("mae_pct"),
                    "strategy_matches": len(strategy_events),
                    "terra_plans": len(plan_events),
                    "decisions": decisions,
                    "blocked_count": len(blocked_events),
                    "final_alerts": len(alert_events),
                    "major_move": major_move,
                    "major_after_setup": major_after_setup,
                    "major_after_terra": major_after_terra,
                    "diagnosis": diagnosis,
                    "reason": reason,
                    "blockers": blockers,
                }
            )

        rows.sort(
            key=lambda row: (
                0 if str(row.get("diagnosis") or "").startswith("MISSED —") else 1,
                0 if row.get("major_move") and not row.get("final_alerts") else 1,
                -(float(row.get("mfe_pct") or 0.0)),
                str(row.get("started_at") or ""),
            )
        )
        return rows

    @staticmethod
    def stats(rows: list[dict[str, Any]]) -> dict[str, int]:
        major = [row for row in rows if row.get("major_move")]
        true_misses = [
            row for row in major
            if str(row.get("diagnosis") or "").startswith("MISSED —")
        ]
        return {
            "major_runners": len(major),
            "alerted": sum(1 for row in major if row.get("final_alerts")),
            "missed": len(true_misses),
            "no_setup": sum(1 for row in true_misses if row.get("diagnosis") == "MISSED — NO SETUP"),
            "late_setup": sum(1 for row in major if row.get("diagnosis") == "LATE — MOVE BEFORE SETUP"),
            "terra_missed": sum(
                1
                for row in true_misses
                if row.get("diagnosis") in {"MISSED — TERRA WATCH", "MISSED — TERRA REJECT"}
            ),
            "terra_filtered_correctly": sum(
                1 for row in major if row.get("diagnosis") == "FILTERED — NO MAJOR MOVE AFTER TERRA"
            ),
            "hard_gate": sum(1 for row in true_misses if row.get("diagnosis") == "MISSED — HARD GATE"),
        }
