from __future__ import annotations

from typing import Any

from . import strategy_library_v2 as v2


STRATEGY_LIBRARY_VERSION = 3


def strategy_catalog() -> list[dict[str, str]]:
    return v2.strategy_catalog()


def _instance_time(existing_key: str) -> str:
    parts = str(existing_key or "").split("|")
    return parts[1] if len(parts) >= 2 and parts[1] else "UNKNOWN"


def _fmt(value: Any) -> str:
    try:
        return f"{float(value):.4f}"
    except (TypeError, ValueError):
        return "UNKNOWN"


def _stabilize_match(match: dict[str, Any]) -> dict[str, Any]:
    """Keep one structural setup from looking new just because live price moved.

    Strategy Library v2 included entry_reference in every setup-instance key. That
    is fine for static breakout levels, but some continuation strategies update
    entry_reference as the current bar changes. On a 30-second scanner this could
    turn the same pullback into a new OpenAI request every scan.
    """
    value = dict(match)
    strategy_id = str(value.get("strategy_id") or "UNKNOWN")
    existing_key = str(value.get("instance_key") or "")
    key_levels = value.get("key_levels") or {}

    if strategy_id == "FIRST_PULLBACK":
        # The pullback-low bar and low define the structural pullback. Recovery
        # price can move every few seconds without making this a new setup.
        value["instance_key"] = (
            f"{strategy_id}|{_instance_time(existing_key)}|PBLOW:{_fmt(key_levels.get('pullback_low'))}"
        )
    elif strategy_id == "VWAP_RECLAIM_HOLD":
        # The reclaim-bar time is the event identity. Session VWAP drifts slightly
        # as new trades arrive and should not create another API call by itself.
        value["instance_key"] = f"{strategy_id}|{_instance_time(existing_key)}"
    elif strategy_id == "VWAP_PULLBACK_HOLD":
        # Tie the instance to the structural pullback low rather than the latest
        # recovery candle/VWAP value, both of which can update each scan.
        value["instance_key"] = (
            f"{strategy_id}|PBLOW:{_fmt(key_levels.get('pullback_low'))}"
        )

    return value


def detect_known_setups(bars: list[dict[str, Any]]) -> dict[str, Any]:
    result = dict(v2.detect_known_setups(bars))
    matches = [_stabilize_match(dict(match)) for match in (result.get("matches") or [])]
    result["matches"] = matches
    result["library_version"] = STRATEGY_LIBRARY_VERSION

    primary = result.get("primary")
    if isinstance(primary, dict):
        stable_primary = _stabilize_match(dict(primary))
        result["primary"] = stable_primary
        result["summary"] = (
            f"Primary setup: {stable_primary.get('name') or stable_primary.get('strategy_id')} "
            f"(score {stable_primary.get('score')}/100, instance {stable_primary.get('instance_key')})."
        )

    return result
