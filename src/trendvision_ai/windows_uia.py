from __future__ import annotations

import logging
import time
from collections.abc import Callable

from .models import CapturedNotification
from .parser import parse_uia_texts

LOGGER = logging.getLogger(__name__)


def _load_pywinauto():
    try:
        from pywinauto import Desktop  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "pywinauto is required on Windows. Run scripts\\setup.bat first."
        ) from exc
    return Desktop


def _extract_window_texts(window) -> list[str]:
    texts: list[str] = []

    try:
        title = window.window_text()
        if title:
            texts.append(title)
    except Exception:
        pass

    try:
        descendants = window.descendants()
    except Exception:
        return texts

    for control in descendants:
        try:
            text = control.window_text()
        except Exception:
            continue
        if text:
            texts.append(text)

    return texts


def listen_for_trendvision_toasts(
    on_notification: Callable[[CapturedNotification], None],
    *,
    poll_interval_seconds: float = 0.35,
    debug: bool = False,
) -> None:
    Desktop = _load_pywinauto()
    desktop = Desktop(backend="uia")
    recently_seen: dict[str, float] = {}

    while True:
        now = time.monotonic()
        try:
            windows = desktop.windows()
        except Exception as exc:
            LOGGER.debug("Could not enumerate desktop windows: %s", exc)
            time.sleep(poll_interval_seconds)
            continue

        for window in windows:
            try:
                texts = _extract_window_texts(window)
            except Exception as exc:
                LOGGER.debug("Failed reading a UIA window: %s", exc)
                continue

            if not texts:
                continue

            joined = "\n".join(texts)
            if "TrendVision" not in joined:
                continue

            if debug:
                LOGGER.info("UIA candidate:\n%s", joined)

            parsed = parse_uia_texts(texts)
            if parsed is None:
                continue

            last_seen = recently_seen.get(parsed.fingerprint)
            if last_seen is not None and now - last_seen < 30:
                continue
            recently_seen[parsed.fingerprint] = now

            on_notification(parsed.to_notification())

        recently_seen = {
            fingerprint: seen_at
            for fingerprint, seen_at in recently_seen.items()
            if now - seen_at < 120
        }
        time.sleep(poll_interval_seconds)
