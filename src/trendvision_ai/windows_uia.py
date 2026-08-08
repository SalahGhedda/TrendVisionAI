from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Iterable

from .models import CapturedNotification
from .parser import parse_uia_texts

LOGGER = logging.getLogger(__name__)
_TRENDVISION_HEADER_RE = re.compile(r"^trendvision\s*\(#", re.IGNORECASE)


def _load_pywinauto():
    try:
        from pywinauto import Desktop  # type: ignore
        from pywinauto.controls.uiawrapper import UIAWrapper  # type: ignore
        from pywinauto.findwindows import find_elements  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "pywinauto is required on Windows. Run scripts\\setup.bat first."
        ) from exc
    return Desktop, UIAWrapper, find_elements


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
    try:
        parts.append(f"control_type={window.element_info.control_type!r}")
    except Exception:
        pass
    geometry = _window_geometry(window)
    if geometry is not None:
        parts.append(f"size={geometry[0]}x{geometry[1]}")
    return ", ".join(parts) or "<metadata unavailable>"


def _element_identity(window) -> str:
    try:
        runtime_id = getattr(window.element_info, "runtime_id", None)
        if runtime_id:
            return f"runtime:{tuple(runtime_id)}"
    except Exception:
        pass

    try:
        return f"handle:{int(window.handle)}"
    except Exception:
        return f"object:{id(window)}"


def _top_level_class(window) -> str:
    try:
        return window.top_level_parent().class_name() or ""
    except Exception:
        return ""


def _is_obvious_app_tree(window) -> bool:
    """Ignore browser/VS Code/Discord Electron trees during toast discovery."""
    top_class = _top_level_class(window).casefold()
    own_class = ""
    try:
        own_class = (window.class_name() or "").casefold()
    except Exception:
        pass

    noisy_markers = ("chrome_widgetwin", "monaco-")
    return any(marker in top_class or marker in own_class for marker in noisy_markers)


def _has_notification_signature(texts: list[str]) -> bool:
    lowered = [text.strip().casefold() for text in texts]
    has_discord = any(text == "discord" or text.startswith("discord notification") for text in lowered)
    has_trendvision_header = any(_TRENDVISION_HEADER_RE.match(text.strip()) for text in texts)
    return has_discord and has_trendvision_header


def _looks_like_discord_toast(window, texts: list[str]) -> bool:
    if not _has_notification_signature(texts):
        return False
    if _is_obvious_app_tree(window):
        return False

    if len(texts) > 60 or sum(len(text) for text in texts) > 6500:
        return False

    geometry = _window_geometry(window)
    if geometry is None:
        return True

    width, height = geometry
    if width <= 0 or height <= 0:
        return False
    return width <= 1200 and height <= 950


def _walk_ancestors(element_info, max_levels: int = 12) -> Iterable[object]:
    current = element_info
    for _ in range(max_levels):
        if current is None:
            return
        yield current
        try:
            current = current.parent
        except Exception:
            return


def _search_named_elements(find_elements):
    """Search only names that closely resemble an actual Windows toast."""
    searches = [
        {"title": "Discord"},
        {"title_re": r"(?i)^discord notification.*"},
        {"title_re": r"(?i)^trendvision\s*\(#.*"},
    ]

    found: list[object] = []
    seen: set[str] = set()

    for selector in searches:
        try:
            elements = find_elements(
                backend="uia",
                top_level_only=False,
                visible_only=True,
                **selector,
            )
        except Exception:
            continue

        for element in elements:
            try:
                runtime_id = getattr(element, "runtime_id", None)
                identity = f"runtime:{tuple(runtime_id)}" if runtime_id else f"object:{id(element)}"
            except Exception:
                identity = f"object:{id(element)}"
            if identity in seen:
                continue
            seen.add(identity)
            found.append(element)

    return found


def _discover_candidate_surfaces(desktop, UIAWrapper, find_elements):
    yielded: set[str] = set()

    try:
        for window in desktop.windows():
            identity = _element_identity(window)
            if identity in yielded:
                continue
            yielded.add(identity)
            yield window
    except Exception:
        pass

    for named_element in _search_named_elements(find_elements):
        for ancestor_info in _walk_ancestors(named_element):
            try:
                surface = UIAWrapper(ancestor_info)
            except Exception:
                continue

            identity = _element_identity(surface)
            if identity in yielded:
                continue
            if _is_obvious_app_tree(surface):
                continue

            geometry = _window_geometry(surface)
            if geometry is not None:
                width, height = geometry
                if width <= 0 or height <= 0 or width > 1200 or height > 950:
                    continue

            try:
                texts = _extract_window_texts(surface)
            except Exception:
                continue
            if not texts or not _has_notification_signature(texts):
                continue

            yielded.add(identity)
            yield surface


def listen_for_trendvision_toasts(
    on_notification: Callable[[CapturedNotification], None],
    *,
    poll_interval_seconds: float = 0.35,
    debug: bool = False,
) -> None:
    Desktop, UIAWrapper, find_elements = _load_pywinauto()
    desktop = Desktop(backend="uia")
    recently_seen: dict[str, float] = {}
    debug_seen: dict[str, float] = {}
    last_debug_heartbeat = 0.0

    while True:
        now = time.monotonic()
        candidate_count = 0

        for window in _discover_candidate_surfaces(desktop, UIAWrapper, find_elements):
            try:
                texts = _extract_window_texts(window)
            except Exception:
                continue

            if not texts or not _looks_like_discord_toast(window, texts):
                continue

            candidate_count += 1
            debug_key = _element_identity(window) + "|" + "|".join(texts[:4])
            if debug and now - debug_seen.get(debug_key, 0.0) > 30:
                debug_seen[debug_key] = now
                LOGGER.info(
                    "Possible Discord toast (%s) sample=%s",
                    _window_debug_description(window),
                    " | ".join(texts[:8]),
                )

            parsed = parse_uia_texts(texts)
            if parsed is None:
                continue

            last_seen = recently_seen.get(parsed.fingerprint)
            if last_seen is not None and now - last_seen < 30:
                continue
            recently_seen[parsed.fingerprint] = now
            on_notification(parsed.to_notification())

        if debug and now - last_debug_heartbeat >= 15:
            LOGGER.debug("UIA listener alive; toast candidates this scan=%d", candidate_count)
            last_debug_heartbeat = now

        recently_seen = {
            fingerprint: seen_at
            for fingerprint, seen_at in recently_seen.items()
            if now - seen_at < 120
        }
        debug_seen = {
            key: seen_at
            for key, seen_at in debug_seen.items()
            if now - seen_at < 120
        }
        time.sleep(poll_interval_seconds)
