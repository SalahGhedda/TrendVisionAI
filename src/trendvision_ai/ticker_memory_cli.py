from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import load_config
from .storage import AlertStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect TrendVisionAI per-ticker memory.")
    parser.add_argument("ticker", nargs="?", help="Optional ticker to inspect, e.g. LRHC.")
    parser.add_argument("--window", type=int, default=30, help="Recent convergence window in minutes.")
    parser.add_argument("--limit", type=int, default=25, help="Number of ticker states to list.")
    parser.add_argument("--no-rebuild", action="store_true", help="Skip rebuilding ticker state from stored events.")
    return parser


def _value(facts: dict, key: str):
    entry = facts.get(key)
    if not isinstance(entry, dict):
        return None
    return entry.get("value")


def _show_one(store: AlertStore, ticker: str, window: int) -> None:
    ticker = ticker.upper().strip()
    states = {item["ticker"]: item for item in store.list_ticker_states(limit=10000)}
    state = states.get(ticker)

    print("=" * 72)
    print(f"TICKER MEMORY: {ticker}")
    print("=" * 72)
    if state is None:
        print("No stored scanner events for this ticker.")
        return

    print(f"First seen : {state['first_seen_at']}")
    print(f"Last seen  : {state['last_seen_at']}")
    print(f"All events : {state['event_count']}")
    print(f"Channels   : {state['channel_count']} -> {', '.join(state['channels'])}")
    print(f"Latest     : {state['latest_event_type']} | {state['latest_headline']}")

    facts = state["facts"]
    preferred = [
        ("price", "Price"),
        ("change_pct", "Change %"),
        ("signal", "Signal"),
        ("relative_volume", "Relative volume"),
        ("float", "Float"),
        ("market_cap", "Market cap"),
        ("zero_borrow", "Zero borrow"),
        ("no_shares_available", "No shares available"),
        ("direction", "Whale direction"),
        ("halt_status", "Halt status"),
        ("headline", "News headline"),
    ]
    visible = [(label, _value(facts, key)) for key, label in preferred if _value(facts, key) is not None]
    if visible:
        print("\nLatest known facts:")
        for label, value in visible:
            print(f"  {label:<20} {value}")

    summary = store.get_convergence_summary(ticker, window_minutes=window)
    print(f"\nRecent convergence ({window} min):")
    print(f"  Events   : {summary['event_count']}")
    print(f"  Channels : {summary['channel_count']} -> {', '.join(summary['channels']) or '-'}")
    for event in summary["events"]:
        print(f"  - {event['received_at']} | #{event['channel']} | {event['headline']}")


def _show_all(store: AlertStore, limit: int, window: int) -> None:
    states = store.list_ticker_states(limit=limit)
    print("TrendVisionAI - Ticker Memory")
    print("=" * 72)
    if not states:
        print("No ticker states yet.")
        return

    for state in states:
        summary = store.get_convergence_summary(state["ticker"], window_minutes=window)
        recent_channels = ",".join(summary["channels"]) or "-"
        print(
            f"{state['ticker']:<8} | total {state['event_count']:>3} events / "
            f"{state['channel_count']:>2} channels | recent {summary['event_count']:>2} / "
            f"{summary['channel_count']:>2} | {recent_channels}"
        )

    print()
    print("Tip: run scripts\\show_ticker_memory.bat TICKER for the full history summary.")


def main() -> int:
    args = _parser().parse_args()
    config_path = Path("config.json")
    config = load_config(config_path if config_path.exists() else None)
    store = AlertStore(config.database_path, config.jsonl_path)

    if not args.no_rebuild:
        rebuilt = store.rebuild_ticker_states()
        print(f"Ticker states rebuilt: {rebuilt}")
        print()

    if args.ticker:
        _show_one(store, args.ticker, args.window)
    else:
        _show_all(store, args.limit, args.window)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
