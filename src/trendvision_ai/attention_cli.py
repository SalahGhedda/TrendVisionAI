from __future__ import annotations

import argparse
from pathlib import Path

from .attention import evaluate_attention
from .config import load_config
from .storage import AlertStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rank recent TrendVision ticker convergence for review attention.")
    parser.add_argument("--window", type=int, default=30, help="Recent window in minutes. Default: 30.")
    parser.add_argument("--limit", type=int, default=20, help="Maximum tickers to display. Default: 20.")
    return parser


def main() -> int:
    args = _parser().parse_args()
    config_path = Path("config.json")
    config = load_config(config_path if config_path.exists() else None)
    store = AlertStore(config.database_path, config.jsonl_path)

    # Keep persistent state aligned with the event table before ranking.
    store.rebuild_ticker_states()
    states = store.list_ticker_states(limit=10000)

    ranked = []
    for state in states:
        summary = store.get_convergence_summary(state["ticker"], window_minutes=args.window)
        if summary["event_count"] == 0:
            continue
        ranked.append(evaluate_attention(summary))

    ranked.sort(key=lambda item: (item.score, item.channel_count, item.event_count), reverse=True)
    ranked = ranked[: max(1, args.limit)]

    print("TrendVisionAI - Attention List")
    print("=" * 88)
    print(f"Window: last {args.window} minutes")
    print("This is a review-priority ranking, NOT a buy/sell signal.")
    print()

    if not ranked:
        print("No ticker scanner events inside the selected window.")
        return 0

    for item in ranked:
        channels = ",".join(item.channels)
        risk = f" | RISK: {'; '.join(item.risk_flags)}" if item.risk_flags else ""
        print(
            f"{item.ticker:<7} | {item.tier:<14} | score {item.score:>2} | "
            f"{item.event_count:>2} events / {item.channel_count} ch | {channels}{risk}"
        )
        if item.reasons:
            print("         " + " ; ".join(item.reasons))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
