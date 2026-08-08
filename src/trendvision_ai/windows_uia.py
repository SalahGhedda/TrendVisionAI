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


def _window_geometry(window) -> tuple[int, int] | None:
    try:
        rect = window.rectangle()
        return int(rect.width()), int(rect.height())
    except Exception:
        return None


def _window_debug_description(window) -> str:
    parts: list[str] = []
    try:
        parts.append(f"title={window.window_text()!r}")
    except Exception:
        pass
    try:
        parts.append(f"class={window.class_name()!r}")
    except Exception:
        pass
    try:
        parts.append(f"pid={window.process_id()}")
    except Exception:
        pass
    geometry = _window_geometry(window)
    if geometry is not None:
        parts.append(f"size={geometry[0]}x{geometry[1]}")
    return ", ".join(parts) or "<metadata unavailable>"


def _looks_like_discord_toast(window, texts: list[str]) -> bool:
    """Reject ordinary app/browser windows before parsing a notification.

    Our first guard only required an exact `Discord` label plus a
    `TrendVision (#...)` line. A browser showing this ChatGPT conversation can
    contain both strings, so it still looked like a notification.

    A live Windows toast is a small top-level surface with relatively little
    accessibility text. Full browser/VS Code windows are much larger and expose
    hundreds of controls. We therefore require both the Discord/TrendVision
    signature *and* toast-like geometry/text volume.
    """
    has_discord_app_label = any(text.strip().casefold() == "discord" for text in texts)
    has_trendvision_header = any(
        text.strip().casefold().startswith("trendvision (#") for text in texts
    )
    if not (has_discord_app_label and has_trendvision_header):
        return False

    # Safety valve: a notification should never expose an entire application's
    # accessibility tree. This also protects us if window geometry is unusual.
    if len(texts) > 45:
        return False
    if sum(len(text) for text in texts) > 5000:
        return False

    geometry = _window_geometry(window)
    if geometry is None:
        # Unknown geometry is allowed for now because Windows builds differ.
        # The text-volume checks above still reject the false positives we saw.
        return True

    width, height = geometry
    if width <= 0 or height <= 0:
        return False

    # Windows 10/11 toast surfaces are compact. Keep generous limits so DPI
    # scaling and accessibility settings do not accidentally reject a real one.
    if width > 1000 or height > 900:
        return False

    return True


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

            has_signature = (
                any(text.strip().casefold() == "discord" for text in texts)
                and any(text.strip().casefold().startswith("trendvision (#") for text in texts)
            )

            if not _looks_like_discord_toast(window, texts):
                if debug and has_signature:
                    LOGGER.info(
                        "Rejected Discord/TrendVision window (%s):\n%s",
                        _window_debug_description(window),
                        "\n".join(texts[:60]),
                    )
                continue

            if debug:
                LOGGER.info(
                    "Discord TrendVision toast candidate (%s):\n%s",
                    _window_debug_description(window),
                    "\n".join(texts),
                )

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
