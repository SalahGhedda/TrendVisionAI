from __future__ import annotations

from datetime import datetime
from typing import Any

from . import desktop_ui_strategy_pipeline as pipeline
from . import desktop_ui_strategy_pipeline_v6 as current
from .live_pipeline import LivePipelineStore
from .strategy_library_v3 import detect_known_setups


# A materially new same-strategy setup may be reviewed again, but not every
# 30-second scan. Different strategy families remain eligible immediately.
SAME_STRATEGY_REVIEW_COOLDOWN_SECONDS = 120.0

# StrategyTradePlanWorker resolves this name from its module at runtime, so this
# upgrades V6 recognition without duplicating the large UI/worker implementation.
pipeline.detect_known_setups = detect_known_setups

_ORIGINAL_EXISTING_PLAN = LivePipelineStore.existing_trade_plan_for_session


def _parse_time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def _existing_plan_with_review_cooldown(
    self: LivePipelineStore,
    session_id: int,
    *,
    strategy_id: str | None = None,
    setup_instance_key: str | None = None,
    limit: int = 500,
) -> dict[str, Any] | None:
    """Deduplicate one exact instance and rate-limit same-strategy re-reviews.

    V6 intentionally allowed materially new setup instances. The first version
    was too permissive for continuation patterns whose live reference can update
    every scan. V7 keeps that capability but requires at least two minutes before
    another instance of the same strategy can spend another OpenAI request.
    """
    exact = _ORIGINAL_EXISTING_PLAN(
        self,
        session_id,
        strategy_id=strategy_id,
        setup_instance_key=setup_instance_key,
        limit=limit,
    )
    if exact is not None:
        return exact

    requested_strategy = str(strategy_id or "").strip()
    if not requested_strategy or not setup_instance_key:
        return None

    latest_same_strategy = _ORIGINAL_EXISTING_PLAN(
        self,
        session_id,
        strategy_id=requested_strategy,
        setup_instance_key=None,
        limit=limit,
    )
    if latest_same_strategy is None:
        return None

    created = _parse_time(latest_same_strategy.get("created_at"))
    if created is None:
        return None
    now = datetime.now().astimezone()
    try:
        age = (now - created.astimezone()).total_seconds()
    except (TypeError, ValueError):
        return None
    if 0.0 <= age < SAME_STRATEGY_REVIEW_COOLDOWN_SECONDS:
        value = dict(latest_same_strategy)
        value["duplicate_reason"] = (
            f"Same-strategy automatic review cooldown: {age:.0f}s elapsed; "
            f"wait {SAME_STRATEGY_REVIEW_COOLDOWN_SECONDS:.0f}s before another {requested_strategy} instance."
        )
        return value
    return None


LivePipelineStore.existing_trade_plan_for_session = _existing_plan_with_review_cooldown


def main() -> int:
    return current.main()


if __name__ == "__main__":
    raise SystemExit(main())
