from __future__ import annotations

import argparse
import logging
import platform
import sys
from pathlib import Path

from .config import load_config
from .models import CapturedNotification
from .storage import AlertStore
from .windows_uia import listen_for_trendvision_toasts


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Listen for TrendVision Discord Windows notifications.")
    parser.add_argument("--config", default="config.json", help="Path to config JSON.")
    parser.add_argument("--debug", action="store_true", help="Log raw UI Automation candidates.")
    return parser


def _print_notification(notification: CapturedNotification, saved: bool) -> None:
    status = "SAVED" if saved else "DUPLICATE"
    print("\n" + "=" * 72)
    print(f"[{notification.received_at}] TRENDVISION ALERT [{status}]")
    print(f"Channel : #{notification.channel or 'unknown'}")
    print(f"Ticker  : {notification.ticker or '-'}")
    if notification.title:
        print(f"Title   : {notification.title}")
    print("Body:")
    print(notification.body or "(no body text exposed by Windows)")
    print("=" * 72, flush=True)


def main() -> int:
    args = _build_parser().parse_args()

    if platform.system() != "Windows":
        print("This listener must be run on Windows because it uses Windows UI Automation.")
        return 2

    config_path = Path(args.config)
    config = load_config(config_path if config_path.exists() else None)

    logging.basicConfig(
        level=logging.INFO if args.debug else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    store = AlertStore(config.database_path, config.jsonl_path)

    def handle(notification: CapturedNotification) -> None:
        saved = store.save(notification)
        _print_notification(notification, saved)

    print("TrendVisionAI Listener v0.1")
    print("Listening for visible Windows notifications containing 'TrendVision'...")
    print("Leave this window open. Press Ctrl+C to stop.")
    print(f"Database: {config.database_path}")

    try:
        listen_for_trendvision_toasts(
            handle,
            poll_interval_seconds=config.poll_interval_seconds,
            debug=args.debug,
        )
    except KeyboardInterrupt:
        print("\nListener stopped.")
        return 0
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
