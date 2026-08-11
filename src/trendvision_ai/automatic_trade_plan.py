from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Any

from . import trade_plan_calibration_v3 as v3
from .api_call_log import OpenAIApiCallStore
from .live_pipeline import MIN_RISK_REWARD_TARGET_1, MIN_RISK_REWARD_TARGET_2


AUTO_PLAN_SOURCE = "AUTO_ALPACA_1M_CHART_STRATEGY_LIBRARY"
AUTO_TRADE_PLAN_MODEL = "gpt-5.6-terra"
AUTO_TRADE_PLAN_REASONING_EFFORT = "medium"


class NoRecognizedStrategyError(RuntimeError):
    pass


def _adapt_auto_text(value: Any) -> str:
    text = str(value or "")
    replacements = (
        ("user-supplied chart screenshot", "automatically generated Alpaca 1-minute chart"),
        ("user supplied chart screenshot", "automatically generated Alpaca 1-minute chart"),
        ("newer chart screenshot", "newer automatically generated Alpaca 1-minute chart context"),
        ("new chart screenshot", "new automatically generated Alpaca 1-minute chart context"),
        ("screenshot", "automatic chart"),
    )
    for source, target in replacements:
        text = text.replace(source, target).replace(source.title(), target)
    return text


def _adapt_auto_result(parsed: dict[str, Any]) -> dict[str, Any]:
    result = dict(parsed)
    for field in ("summary", "setup_type", "entry_trigger", "invalidation"):
        result[field] = _adapt_auto_text(result.get(field))
    for field in ("positive_factors", "risk_factors", "chart_observations", "what_to_confirm"):
        result[field] = list(
            dict.fromkeys(
                _adapt_auto_text(value)
                for value in (result.get(field) or [])
                if str(value or "").strip()
            )
        )
    return result


def _apply_strategy_guardrails(
    parsed: dict[str, Any],
    strategy_context: dict[str, Any],
) -> dict[str, Any]:
    result = dict(parsed)
    primary = strategy_context.get("primary") or {}
    strategy_name = str(primary.get("name") or "").strip()
    if strategy_name:
        result["setup_type"] = strategy_name

    if str(result.get("decision") or "").upper() != "POTENTIAL TRADE":
        return result

    key_levels = primary.get("key_levels") or {}
    constraints = primary.get("plan_constraints") or {}
    reference = v3.base._num(key_levels.get("entry_reference"))
    entry_high = v3.base._num(result.get("entry_high"))
    stop = v3.base._num(result.get("stop_loss"))
    target_1 = v3.base._num(result.get("target_1"))
    target_2 = v3.base._num(result.get("target_2"))
    max_extension = v3.base._num(constraints.get("max_entry_extension_pct"))
    max_extension = max_extension if max_extension is not None else 5.0

    blockers: list[str] = []
    if reference is not None and reference > 0 and entry_high is not None:
        extension = (entry_high / reference - 1.0) * 100.0
        if extension > max_extension:
            blockers.append(
                f"The proposed entry is {extension:.2f}% above the recognized {strategy_name or 'strategy'} reference; "
                f"the strategy-library anti-chase limit is {max_extension:.2f}%."
            )

    if (
        entry_high is not None
        and stop is not None
        and target_1 is not None
        and target_2 is not None
        and entry_high > stop
    ):
        risk = entry_high - stop
        rr_1 = (target_1 - entry_high) / risk
        rr_2 = (target_2 - entry_high) / risk
        if rr_1 + 1e-9 < MIN_RISK_REWARD_TARGET_1:
            blockers.append(
                f"Natural Target 1 offers only {rr_1:.2f}R from the worst price in the entry zone; "
                f"the final alert policy requires at least {MIN_RISK_REWARD_TARGET_1:.2f}R."
            )
        if rr_2 + 1e-9 < MIN_RISK_REWARD_TARGET_2:
            blockers.append(
                f"Natural Target 2 offers only {rr_2:.2f}R from the worst price in the entry zone; "
                f"the final alert policy requires at least {MIN_RISK_REWARD_TARGET_2:.2f}R."
            )

    if blockers:
        result["decision"] = "WATCH"
        for key in ("entry_low", "entry_high", "stop_loss", "target_1", "target_2"):
            result[key] = None
        result["risk_factors"] = list(
            dict.fromkeys([*(result.get("risk_factors") or []), *blockers])
        )
    return result


def analyze_automatic_trade_plan(
    snapshot: dict[str, Any],
    *,
    image_path: str | Path,
    bars: list[dict[str, Any]],
    strategy_context: dict[str, Any],
    api_key: str,
    model: str,
    database_path: str | Path | None = Path("data") / "openai_api_calls.db",
) -> v3.base.TradePlanResult:
    """Run Trade Plan v3 inside a deterministic recognized setup framework."""
    from openai import OpenAI

    primary = strategy_context.get("primary") or {}
    if not strategy_context.get("recognized") or not primary.get("strategy_id"):
        raise NoRecognizedStrategyError(
            "No configured strategy-library setup is currently recognized; OpenAI trade-plan analysis is skipped."
        )

    effective_model = AUTO_TRADE_PLAN_MODEL

    request_snapshot = v3._normalize_unobserved_zero_borrow(snapshot)
    market = request_snapshot.get("alpaca_market_context") or {}
    if not market.get("current_context_usable"):
        raise v3.StaleMarketDataError(v3._freshness_error(market))

    image_url, image_sha = v3.base._image_data_url(image_path)
    request_snapshot = copy.deepcopy(request_snapshot)
    request_snapshot["trade_plan_version"] = v3.TRADE_PLAN_VERSION
    request_snapshot["plan_source"] = AUTO_PLAN_SOURCE
    request_snapshot["automatic_model_config"] = {
        "model": effective_model,
        "reasoning_effort": AUTO_TRADE_PLAN_REASONING_EFFORT,
    }
    request_snapshot["strategy_context"] = copy.deepcopy(strategy_context)
    request_snapshot["automatic_chart_context"] = {
        "source": "Alpaca same-day 1-minute momentum context from 04:00 New York when available, including premarket plus regular session",
        "feed": market.get("feed"),
        "bar_count": len(bars),
        "bars": bars,
        "premarket_context": copy.deepcopy(strategy_context.get("premarket_context") or {}),
        "interpretation": (
            "The image is rendered by TrendVisionAI from these same-feed bars. Earlier bars can be premarket context; "
            "actionable current price/quote still comes from alpaca_market_context during the regular session. "
            "The chart contains no trade recommendation overlay and inherits the limitations of the selected Alpaca feed."
        ),
    }
    request_snapshot["chart_screenshot"] = {
        "sha256": image_sha,
        "role": (
            "TrendVisionAI-generated chart from Alpaca 1-minute bars, potentially including premarket context. "
            "No third-party BUY/SELL/SL/TP recommendation overlay is present."
        ),
    }

    auto_instructions = (
        "KNOWN STRATEGY MODE:\n"
        "- strategy_context is a deterministic TrendVisionAI strategy-library result. The primary recognized setup is the trading framework for this review.\n"
        "- Do NOT invent or switch to a different setup just because another chart pattern looks interesting. Decide whether the recognized setup instance is executable now.\n"
        "- Use the primary strategy key_levels as structural references. Entry must be anchored to the strategy trigger/retest framework rather than an arbitrary percentage from current price.\n"
        "- Respect plan_constraints, especially the volatility-aware anti-chase maximum entry extension.\n"
        "- setup_type must describe the primary recognized strategy.\n"
        "- Historical calibration is a later validator/filter. The recognized setup itself comes from the strategy library, not from a learned ticker-specific rule.\n\n"
        "RISK / REWARD DISCIPLINE:\n"
        f"- The deterministic final alert gate requires natural Target 1 risk/reward of at least {MIN_RISK_REWARD_TARGET_1:.1f}R and Target 2 of at least {MIN_RISK_REWARD_TARGET_2:.1f}R. Risk/reward is measured conservatively from entry_high to stop_loss.\n"
        "- Do NOT move a target farther away merely to satisfy those thresholds. Targets must remain justified by visible structure/resistance or a defensible continuation objective.\n"
        "- If the recognized setup cannot naturally support those risk/reward thresholds at a non-chased entry, return WATCH rather than POTENTIAL TRADE.\n\n"
        "PREMARKET / MOMENTUM CONTEXT:\n"
        "- strategy_context.premarket_context may contain same-feed 04:00-09:30 New York high/low/volume context. Premarket levels are structural references, not proof that a regular-session breakout will continue.\n"
        "- The final actionable decision is still for the regular session; do not create a premarket execution instruction.\n"
        "- Recent 1-minute volatility can legitimately be much larger for fast momentum stocks, so use the supplied adaptive plan_constraints rather than imposing an arbitrary universal 3-5% chase rule.\n\n"
        "AUTOMATIC CHART MODE:\n"
        "- The image was generated by TrendVisionAI from the structured automatic_chart_context bars.\n"
        "- Earlier bars may be premarket; exact current planning price/quote still comes from alpaca_market_context.\n"
        "- The image contains no third-party trade recommendation levels.\n"
        "- If feed=IEX, both the rendered bars and live quote/trade observations are partial-venue evidence.\n\n"
        + v3._INSTRUCTIONS_V3
    )

    ticker = str(request_snapshot.get("ticker") or "?").upper()
    call_store: OpenAIApiCallStore | None = None
    call_id: int | None = None
    started = time.perf_counter()
    if database_path is not None:
        try:
            call_store = OpenAIApiCallStore(database_path)
            call_id = call_store.start_call(
                ticker=ticker,
                purpose="AUTOMATIC TRADE PLAN",
                model=effective_model,
                reasoning_effort=AUTO_TRADE_PLAN_REASONING_EFFORT,
                strategy_id=str(primary.get("strategy_id") or "") or None,
                strategy_name=str(primary.get("name") or "") or None,
                strategy_score=int(primary.get("score")) if primary.get("score") is not None else None,
            )
        except Exception:
            call_store = None
            call_id = None

    client = OpenAI(api_key=api_key)
    raw_response_text = ""
    try:
        response = client.responses.create(
            model=effective_model,
            reasoning={"effort": AUTO_TRADE_PLAN_REASONING_EFFORT},
            instructions=auto_instructions,
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": json.dumps(request_snapshot, ensure_ascii=False, separators=(",", ":")),
                        },
                        {"type": "input_image", "image_url": image_url, "detail": "high"},
                    ],
                }
            ],
            text={"format": v3._SCHEMA_V3},
        )
        raw_response_text = str(response.output_text or "")
        parsed = v3.calibrate_trade_plan_payload_v3(
            json.loads(raw_response_text),
            request_snapshot,
        )
        parsed = _adapt_auto_result(parsed)
        parsed = _apply_strategy_guardrails(parsed, strategy_context)
    except Exception as exc:
        if call_store is not None and call_id is not None:
            try:
                call_store.finish_call(
                    call_id,
                    status="FAILED",
                    duration_ms=round((time.perf_counter() - started) * 1000),
                    error_text=f"{type(exc).__name__}: {exc}",
                    response_text=raw_response_text,
                )
            except Exception:
                pass
        raise

    if call_store is not None and call_id is not None:
        try:
            call_store.finish_call(
                call_id,
                status="COMPLETED",
                duration_ms=round((time.perf_counter() - started) * 1000),
                decision=str(parsed.get("decision") or ""),
                response_text=raw_response_text,
            )
        except Exception:
            pass

    entry_high = v3.base._num(parsed.get("entry_high"))
    stop = v3.base._num(parsed.get("stop_loss"))
    target_1 = v3.base._num(parsed.get("target_1"))
    target_2 = v3.base._num(parsed.get("target_2"))

    return v3.base.TradePlanResult(
        ticker=ticker,
        model=effective_model,
        decision=str(parsed["decision"]),
        confidence=str(parsed["confidence"]),
        risk_level=str(parsed["risk_level"]),
        chart_structure=str(parsed["chart_structure"]),
        setup_type=str(parsed.get("setup_type") or primary.get("name") or ""),
        summary=str(parsed.get("summary") or ""),
        entry_low=v3.base._num(parsed.get("entry_low")),
        entry_high=entry_high,
        stop_loss=stop,
        target_1=target_1,
        target_2=target_2,
        risk_reward_target_1=v3.base._risk_reward(entry_high, stop, target_1),
        risk_reward_target_2=v3.base._risk_reward(entry_high, stop, target_2),
        entry_trigger=str(parsed.get("entry_trigger") or ""),
        invalidation=str(parsed.get("invalidation") or ""),
        positive_factors=[str(value) for value in parsed.get("positive_factors") or []],
        risk_factors=[str(value) for value in parsed.get("risk_factors") or []],
        chart_observations=[str(value) for value in parsed.get("chart_observations") or []],
        what_to_confirm=[str(value) for value in parsed.get("what_to_confirm") or []],
        created_at=v3.base._now_iso(),
        plan_version=v3.TRADE_PLAN_VERSION,
    )
