from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable

from .models import CapturedNotification
from .parser import parse_uia_texts

LOGGER = logging.getLogger(__name__)


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


def _has_notification_signature(texts: list[str]) -> bool:
    lowered = [text.strip().casefold() for text in texts]
    has_discord = any("discord" in text for text in lowered)
    has_trendvision = any("trendvision" in text for text in lowered)
    return has_discord and has_trendvision


def _looks_like_discord_toast(window, texts: list[str]) -> bool:
    """Reject ordinary app/browser windows before parsing a notification."""
    if not _has_notification_signature(texts):
        return False

    # A real toast should expose only a small accessibility subtree. This
    # rejects VS Code/browser windows that happen to display our own docs/chat.
    if len(texts) > 60:
        return False
    if sum(len(text) for text in texts) > 6500:
        return False

    geometry = _window_geometry(window)
    if geometry is None:
        return True

    width, height = geometry
    if width <= 0 or height <= 0:
        return False

    # Generous limits for DPI scaling. The user's real toast is much smaller.
    if width > 1200 or height > 950:
        return False

    return True


def _element_identity(window) -> str:
    """Best-effort identity used only to avoid scanning the same surface twice."""
    try:
        info = window.element_info
        runtime_id = getattr(info, "runtime_id", None)
        if runtime_id:
            return f"runtime:{tuple(runtime_id)}"
    except Exception:
        pass

    try:
        return f"handle:{int(window.handle)}"
    except Exception:
        pass

    return f"object:{id(window)}"


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


def _search_named_elements(find_elements, *, debug: bool):
    """Find UIA elements that may belong to a Discord/TrendVision toast.

    Different Windows/Discord builds expose different accessible names. Some
    report an exact `Discord` label, while others expose phrases such as
    `Discord notification` or put TrendVision in a separate child. In debug
    mode we therefore perform a few broader searches too.
    """
    searches = [
        ("Discord exact", {"title": "Discord"}),
        ("Discord fuzzy", {"title_re": r"(?i).*discord.*"}),
        ("TrendVision fuzzy", {"title_re": r"(?i).*trendvision.*"}),
    ]

    found: list[object] = []
    seen_runtime_ids: set[str] = set()

    for label, selector in searches:
        try:
            elements = find_elements(
                backend="uia",
                top_level_only=False,
                visible_only=True,
                **selector,
            )
        except Exception as exc:
            if debug:
                LOGGER.debug("UIA search %s failed: %s", label, exc)
            continue

        if debug:
            LOGGER.debug("UIA search %s found %d visible element(s).", label, len(elements))

        for element in elements:
            try:
                runtime_id = getattr(element, "runtime_id", None)
                identity = f"runtime:{tuple(runtime_id)}" if runtime_id else f"object:{id(element)}"
            except Exception:
                identity = f"object:{id(element)}"
            if identity in seen_runtime_ids:
                continue
            seen_runtime_ids.add(identity)
            found.append(element)

    return found


def _discover_candidate_surfaces(desktop, UIAWrapper, find_elements, *, debug: bool):
    """Yield possible toast surfaces.

    A Windows toast is not guaranteed to be a top-level window. It may be a
    nested subtree owned by the Windows shell. We therefore inspect both normal
    top-level windows and ancestors of UIA elements whose accessible names look
    related to Discord or TrendVision.
    """
    yielded: set[str] = set()

    try:
        for window in desktop.windows():
            identity = _element_identity(window)
            if identity in yielded:
                continue
            yielded.add(identity)
            yield window
    except Exception as exc:
        LOGGER.debug("Could not enumerate top-level desktop windows: %s", exc)

    named_elements = _search_named_elements(find_elements, debug=debug)

    for named_element in named_elements:
        if debug:
            try:
                LOGGER.debug(
                    "Named UIA element: name=%r control_type=%r class=%r",
                    getattr(named_element, "name", None),
                    getattr(named_element, "control_type", None),
                    getattr(named_element, "class_name", None),
                )
            except Exception:
                pass

        for ancestor_info in _walk_ancestors(named_element):
            try:
                surface = UIAWrapper(ancestor_info)
            except Exception:
                continue

            identity = _element_identity(surface)
            if identity in yielded:
                continue

            try:
                texts = _extract_window_texts(surface)
            except Exception:
                continue
            if not texts:
                continue

            if debug and any(
                ("discord" in text.casefold() or "trendvision" in text.casefold())
                for text in texts
            ):
                LOGGER.debug(
                    "Ancestor probe (%s):\n%s",
                    _window_debug_description(surface),
                    "\n".join(texts[:80]),
                )

            if not _has_notification_signature(texts):
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

    while True:
        now = time.monotonic()

        for window in _discover_candidate_surfaces(
            desktop,
            UIAWrapper,
            find_elements,
            debug=debug,
        ):
            try:
                texts = _extract_window_texts(window)
            except Exception as exc:
                LOGGER.debug("Failed reading a UIA surface: %s", exc)
                continue

            if not texts:
                continue

            has_signature = _has_notification_signature(texts)

            if not _looks_like_discord_toast(window, texts):
                if debug and has_signature:
                    LOGGER.info(
                        "Rejected Discord/TrendVision surface (%s):\n%s",
                        _window_debug_description(window),
                        "\n".join(texts[:80]),
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
