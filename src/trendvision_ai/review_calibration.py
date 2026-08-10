from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timezone
from typing import Any

from . import ai_review as base


REVIEW_VERSION = 3


_SUPPORTED_FUTURE_OBSERVATIONS = [
    "all-in-one-scanner: ticker/rank plus any explicit signal, change %, price, FT, MC, RV, 1V, news, 0-borrow, CTB, SI or SEC continuation that Windows actually exposes",
    "volume-scanner: ticker, alert number and alert price-move percentage",
    "potential-squeeze-alerts: ticker, alert number and alert price-move percentage",
    "whale-scanner: ticker and whether price is increasing or dropping",
    "0-borrow-scanner: ticker and no-shares-available event",
    "halt-scanner: ticker and HALTED / HALTED UP / HALTED DOWN event",
    "news-scanner: ticker and headline when Windows exposes it",
]


_INSTRUCTIONS_V3 = """You are the calibrated candidate-review layer of TrendVisionAI.
Analyze ONLY the supplied TrendVision case file. Do not browse, invent current market data, or fill missing fields from memory.

Your job is human triage, not order execution. Score four separate dimensions:
- INTEREST: how unusual and worthy of attention the scanner convergence is.
- RISK: how dangerous, extended, volatile, halted or chase-prone the situation is.
- EVIDENCE QUALITY: how complete and internally reliable the captured evidence is.
- REVIEW STATUS: IGNORE, WATCH, WAIT FOR CONFIRMATION, POTENTIAL SETUP or AVOID. This is a review label, not a trade instruction.

Critical interpretation rules:
- Independent scanner convergence is stronger than repeated alerts from one scanner.
- Explicit MOMENTUM/BREAKOUT and explicit RV/relative_volume can be meaningful. Do not invent the meaning of unknown signal labels.
- Very large positive moves increase both interest and chase/gap risk.
- Multiple halts in a short window are a major risk factor and generally imply HIGH or EXTREME risk.
- DIFFERENT PRICE OR CHANGE VALUES AT DIFFERENT TIMESTAMPS ARE NORMAL TIME-SERIES UPDATES, NOT DATA CONFLICTS. Only call them a conflict when the same event/same timestamp, or structured data versus the raw text for that same event, contradicts itself.
- HALTED UP and HALTED DOWN at different timestamps are chronological halt events, not contradictory data. They increase volatility risk but should not be listed as a data conflict merely because the status changes over time.
- A country flag/origin is neutral unless the supplied case contains a concrete listing/regulatory problem.
- Market cap alone does not prove liquidity.
- 'Known Runner' is descriptive metadata. Do not treat it as evidence of future performance, pump/manipulation, or a risk factor by itself.
- Do not treat an alert percentage as relative volume unless an explicit RV/relative_volume field exists.
- In historical all-in-one records, zero_borrow=false may mean the 0-borrow continuation was simply not observed. Treat absence as UNKNOWN, not as proof shares were available.
- The local attention score is only triage and must not control your conclusion.
- Missing fields remain unknown. Do not infer them.
- For 'What TrendVision should show next', request only observations listed in available_future_observations. Do not ask this application to produce options flow, block trades, Level 2/order-book data, explicit UNHALT messages, Reg SHO notes, or other feeds it does not capture.
- Missing-information items should be material to this review. Do not pad the list with unrelated data such as options activity when it is not part of this feed.
- POTENTIAL SETUP requires reasonably consistent evidence. EXTREME risk or an unresolved same-event data conflict should normally mean WAIT FOR CONFIRMATION or AVOID.
- Never promise profit and never issue an automatic order instruction.

Return a concise structured review."""


_UNSUPPORTED_TERMS = re.compile(
    r"(?:options?\s+(?:flow|activity)|block trades?|level[ -]?2|order[- ]?book|reg\s*sho|explicit\s+unhalt|unhalt\s+(?:message|event))",
    re.IGNORECASE,
)


def _normalize_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Normalize legacy semantics before the model sees the case file."""
    value = copy.deepcopy(snapshot)
    value["review_version"] = REVIEW_VERSION
    value["available_future_observations"] = list(_SUPPORTED_FUTURE_OBSERVATIONS)

    # Older all-in-one events used False as the default for zero_borrow even
    # when the Windows toast said nothing about borrow availability. That False
    # means "not observed", not an explicit negative observation.
    facts = ((value.get("ticker_memory") or {}).get("latest_known_facts") or {})
    zero_borrow_fact = facts.get("zero_borrow")
    if isinstance(zero_borrow_fact, dict) and zero_borrow_fact.get("value") is False:
        facts.pop("zero_borrow", None)

    for event in ((value.get("recent_convergence") or {}).get("events") or []):
        if event.get("channel") != "all-in-one-scanner":
            continue
        data = event.get("data") or {}
        if data.get("zero_borrow") is False:
            data.pop("zero_borrow", None)

    return value


def _is_temporal_false_conflict(text: str) -> bool:
    low = text.casefold()
    explicit_same_event = any(
        marker in low
        for marker in (
            "same event",
            "same alert",
            "same timestamp",
            "structured",
            "raw text",
            "raw payload",
        )
    )
    if explicit_same_event:
        return False

    temporal_words = ("differ", "different", "vary", "inconsistent", "conflict", "contradict")
    if any(word in low for word in temporal_words):
        if "price" in low or "change" in low or "change_pct" in low:
            return True
        if "halt" in low and any(word in low for word in ("toggle", "between", "up", "down", "sequence")):
            return True
    return False


def _clean_items(values: Any, *, remove_temporal_conflicts: bool = False) -> list[str]:
    result: list[str] = []
    for raw in values or []:
        text = str(raw).strip()
        if not text:
            continue
        if _UNSUPPORTED_TERMS.search(text):
            continue
        if "known runner" in text.casefold():
            continue
        if remove_temporal_conflicts and _is_temporal_false_conflict(text):
            continue
        result.append(text)
    return list(dict.fromkeys(result))


def calibrate_v3(parsed: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    """Run existing guardrails, then remove known false-positive reasoning."""
    result = base.calibrate_review_payload(parsed, snapshot)

    result["positive_factors"] = _clean_items(result.get("positive_factors"))
    result["risk_factors"] = _clean_items(result.get("risk_factors"))
    result["data_conflicts"] = _clean_items(
        result.get("data_conflicts"), remove_temporal_conflicts=True
    )
    result["missing_information"] = _clean_items(result.get("missing_information"))
    result["next_signals_to_watch"] = _clean_items(result.get("next_signals_to_watch"))
    result["invalidation_warnings"] = _clean_items(result.get("invalidation_warnings"))

    # Re-apply evidence/status after false temporal conflicts are removed.
    evidence = str(result.get("evidence_quality") or "MEDIUM").upper()
    if result["data_conflicts"]:
        evidence = base._maximum_level(evidence, "MEDIUM", base._EVIDENCE_ORDER)
    result["evidence_quality"] = evidence

    status = str(result.get("review_status") or "WATCH").upper()
    risk = str(result.get("risk_level") or "MODERATE").upper()
    if status == "POTENTIAL SETUP" and (
        risk == "EXTREME" or evidence == "LOW" or bool(result["data_conflicts"])
    ):
        status = "WAIT FOR CONFIRMATION"
    result["review_status"] = status
    return result


def analyze_snapshot_v3(
    snapshot: dict[str, Any],
    *,
    api_key: str,
    model: str = base.DEFAULT_MODEL,
) -> base.AIReviewResult:
    from openai import OpenAI

    normalized = _normalize_snapshot(snapshot)
    schema = copy.deepcopy(base._REVIEW_SCHEMA)
    schema["name"] = "trendvision_candidate_review_v3"

    client = OpenAI(api_key=api_key)
    response = client.responses.create(
        model=model,
        instructions=_INSTRUCTIONS_V3,
        input=json.dumps(normalized, ensure_ascii=False, separators=(",", ":")),
        text={"format": schema},
    )
    parsed = json.loads(response.output_text)
    parsed = calibrate_v3(parsed, normalized)
    ticker = str(normalized.get("ticker") or "?").upper()

    return base.AIReviewResult(
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
        review_version=REVIEW_VERSION,
    )
