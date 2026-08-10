from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .config import load_config
from .notification_api_probe import _extract_text, _get_listener
from .parser import parse_uia_texts
from .scanner_events import build_scanner_events
from .storage import AlertStore


def _configure_stdio_utf8() -> None:
    """Make listener output safe across Windows locale/code-page settings.

    Discord/WinRT notification text can contain Unicode formatting controls,
    emoji, flags, smart punctuation and non-Latin characters. Some Windows
    installations still expose stdout/stderr as cp1252, which can crash the
    listener merely while printing a notification. The raw notification is
    already stored as UTF-8, so the diagnostic stream should be UTF-8 too.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="backslashreplace")
            except (OSError, ValueError):
                pass


_configure_stdio_utf8()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Continuously listen for TrendVision Discord notifications using the Windows notification API."
    )
    parser.add_argument("--config", default="config.json", help="Path to config JSON.")
    parser.add_argument(
        "--poll-ms",
        type=int,
        default=250,
        help="Polling interval in milliseconds. Default: 250.",
    )
    return parser


def _app_name(notification) -> str:
    try:
        return str(notification.app_info.display_info.display_name)
    except Exception:
        return "<unknown app>"


def _notification_id(notification) -> int | str:
    try:
        return int(notification.id)
    except Exception:
        return repr(notification)


def _looks_relevant(app_name: str, lines: list[str]) -> bool:
    return "discord" in app_name.casefold() or any(
        "trendvision" in line.casefold() for line in lines
    )


def _print_raw_candidate(app_name: str, notification_id: int | str, lines: list[str]) -> None:
    print("\n" + "-" * 72)
    print(f"WINDOWS NOTIFICATION CANDIDATE | App={app_name} | ID={notification_id}")
    if lines:
        for line in lines:
            print(line)
    else:
        print("(Windows exposed no text lines)")
    print("-" * 72, flush=True)


def _save_raw_candidate(
    path: Path,
    *,
    app_name: str,
    notification_id: int | str,
    lines: list[str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "captured_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "app_name": app_name,
        "notification_id": notification_id,
        "lines": lines,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _print_saved(notification, saved: bool, event_saved: bool | None = None) -> None:
    status = "SAVED" if saved else "DUPLICATE"
    channel = (notification.channel or "unknown").casefold()

    print("\n" + "=" * 72)
    print(f"[{notification.received_at}] TRENDVISION ALERT [{status}]")
    print(f"Channel : #{notification.channel or 'unknown'}")

    if channel == "social-news":
        print("Body:")
        print(notification.body or "(no body text exposed by Windows)")
    else:
        if notification.ticker:
            print(f"Ticker  : {notification.ticker}")
        print("Payload:")
        print(notification.body or "(no body text exposed by Windows)")

    if event_saved is not None:
        print(f"Scanner event(s): {'SAVED' if event_saved else 'PARTIAL/DUPLICATE'}")
    print("=" * 72, flush=True)


def _refresh_and_print_memory(store: AlertStore, tickers: list[str], window_minutes: int = 30) -> None:
    for ticker in dict.fromkeys(value.upper() for value in tickers if value):
        state = store.refresh_ticker_state(ticker)
        if state is None:
            continue
        summary = store.get_convergence_summary(ticker, window_minutes=window_minutes)
        channels = ", ".join(summary["channels"]) or "-"
        print(
            f"TICKER MEMORY | {ticker} | total={state.event_count} events / "
            f"{state.channel_count} channels | recent {window_minutes}m="
            f"{summary['event_count']} events / {summary['channel_count']} channels "
            f"[{channels}]",
            flush=True,
        )


async def main() -> int:
    args = _build_parser().parse_args()

    try:
        from winrt.windows.ui.notifications import NotificationKinds
        from winrt.windows.ui.notifications.management import (
            UserNotificationListener,
            UserNotificationListenerAccessStatus,
        )
    except Exception as exc:
        print("ERROR: WinRT notification packages are not installed correctly.")
        print(f"{type(exc).__name__}: {exc}")
        print("Run scripts\\setup.bat after git pull.")
        return 1

    listener = _get_listener(UserNotificationListener)
    status = listener.get_access_status()
    allowed = getattr(UserNotificationListenerAccessStatus, "ALLOWED", 1)

    if status != allowed:
        print("Windows notification access is not currently allowed.")
        print("Run scripts\\test_notification_api.bat first.")
        return 2

    config_path = Path(args.config)
    config = load_config(config_path if config_path.exists() else None)
    store = AlertStore(config.database_path, config.jsonl_path)
    raw_candidate_path = Path("data/winrt_candidates.jsonl")

    poll_seconds = max(args.poll_ms, 100) / 1000.0
    seen_ids: set[tuple[str, int | str]] = set()
    scans = 0

    # Backfill the durable ticker_state table from everything already captured.
    rebuilt = store.rebuild_ticker_states()

    print("TrendVisionAI - Continuous Windows Notification Listener")
    print("Notification access: ALLOWED")
    print(f"Polling every {int(poll_seconds * 1000)} ms")
    print("Keep this window open BEFORE the next Discord popup appears.")
    print("Press Ctrl+C to stop.")
    print(f"Database: {config.database_path}")
    print(f"Raw WinRT log: {raw_candidate_path}")
    print(f"Ticker memory loaded: {rebuilt} ticker(s)")

    try:
        while True:
            try:
                notifications = await listener.get_notifications_async(NotificationKinds.TOAST)
                notifications = list(notifications)
            except Exception as exc:
                print(f"Notification read error: {type(exc).__name__}: {exc}", flush=True)
                await asyncio.sleep(1.0)
                continue

            scans += 1
            for item in notifications:
                app_name = _app_name(item)
                notification_id = _notification_id(item)
                identity = (app_name, notification_id)

                if identity in seen_ids:
                    continue
                seen_ids.add(identity)

                lines = _extract_text(item)
                if not _looks_relevant(app_name, lines):
                    continue

                _save_raw_candidate(
                    raw_candidate_path,
                    app_name=app_name,
                    notification_id=notification_id,
                    lines=lines,
                )
                _print_raw_candidate(app_name, notification_id, lines)

                parsed = parse_uia_texts([app_name, *lines])
                if parsed is None:
                    print(
                        "Relevant notification detected, but TrendVision parser did not match it yet. "
                        "Raw payload was saved to data\\winrt_candidates.jsonl.",
                        flush=True,
                    )
                    continue

                captured = parsed.to_notification()
                saved = store.save(captured)
                events = build_scanner_events(captured)
                event_results = [store.save_scanner_event(event) for event in events]
                event_saved = all(event_results) if event_results else None
                _print_saved(captured, saved, event_saved)

                tickers = [event.ticker for event in events if event.ticker]
                if tickers:
                    _refresh_and_print_memory(store, tickers)

            if scans % max(1, int(15 / poll_seconds)) == 0:
                print(
                    f"Listener alive | current Windows toasts={len(notifications)} | "
                    f"seen IDs={len(seen_ids)}",
                    flush=True,
                )

            await asyncio.sleep(poll_seconds)
    except KeyboardInterrupt:
        print("\nListener stopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
