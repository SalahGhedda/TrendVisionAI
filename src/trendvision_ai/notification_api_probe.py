from __future__ import annotations

import asyncio
import traceback


def _get_listener(UserNotificationListener):
    # PyWinRT versions have exposed this static property with slightly different
    # Python spellings over time. Support both so the probe is resilient.
    if hasattr(UserNotificationListener, "get_current"):
        return UserNotificationListener.get_current()
    if hasattr(UserNotificationListener, "current"):
        return UserNotificationListener.current
    raise RuntimeError("Could not obtain UserNotificationListener.Current")


def _enum_name(value) -> str:
    name = getattr(value, "name", None)
    return str(name or value)


def _extract_text(notification) -> list[str]:
    texts: list[str] = []
    try:
        visual = notification.notification.visual
    except Exception:
        return texts

    bindings = []
    try:
        bindings = list(visual.bindings)
    except Exception:
        pass

    if not bindings:
        try:
            from winrt.windows.ui.notifications import KnownNotificationBindings

            getter = getattr(KnownNotificationBindings, "get_toast_generic", None)
            if getter is not None:
                binding = visual.get_binding(getter())
                if binding is not None:
                    bindings = [binding]
        except Exception:
            pass

    for binding in bindings:
        try:
            elements = binding.get_text_elements()
        except Exception:
            continue
        try:
            for element in elements:
                text = getattr(element, "text", None)
                if text:
                    texts.append(str(text))
        except TypeError:
            # Older projections exposed an iterator object instead of a normal
            # Python iterable. Best effort fallback.
            try:
                iterator = iter(elements)
                while getattr(iterator, "has_current", False):
                    current = getattr(iterator, "current", None)
                    text = getattr(current, "text", None)
                    if text:
                        texts.append(str(text))
                    next(iterator, None)
            except Exception:
                pass

    # Preserve order while removing duplicates.
    return list(dict.fromkeys(texts))


async def main() -> int:
    print("TrendVisionAI - Windows Notification API probe")
    print("This test does NOT use UI Automation.")
    print()

    try:
        from winrt.windows.ui.notifications import NotificationKinds
        from winrt.windows.ui.notifications.management import (
            UserNotificationListener,
            UserNotificationListenerAccessStatus,
        )
    except Exception as exc:
        print("ERROR: WinRT notification packages are not installed correctly.")
        print(f"{type(exc).__name__}: {exc}")
        print("Run scripts\\setup.bat again after git pull.")
        return 1

    try:
        listener = _get_listener(UserNotificationListener)
    except Exception as exc:
        print("ERROR: Windows would not give us UserNotificationListener.Current")
        print(f"{type(exc).__name__}: {exc}")
        return 1

    print("Listener object: OK")

    try:
        status = listener.get_access_status()
        print(f"Current access status: {_enum_name(status)}")
    except Exception as exc:
        print(f"Could not read current access status: {type(exc).__name__}: {exc}")
        status = None

    allowed = getattr(UserNotificationListenerAccessStatus, "ALLOWED", 1)
    if status != allowed:
        print("Requesting Windows notification access...")
        try:
            status = await listener.request_access_async()
            print(f"Access request result: {_enum_name(status)}")
        except Exception as exc:
            print()
            print("ACCESS REQUEST FAILED")
            print(f"{type(exc).__name__}: {exc}")
            print()
            print("This API is permission-protected. If Windows refuses access from")
            print("our plain Python process, the next step is to give TrendVisionAI")
            print("Windows package identity (MSIX/sparse package) and declare the")
            print("userNotificationListener capability. That is expected on some")
            print("Windows configurations.")
            return 2

    if status != allowed:
        print()
        print("Windows did not grant notification access.")
        print(f"Final status: {_enum_name(status)}")
        print("We will need to package/register TrendVisionAI with the")
        print("userNotificationListener capability before continuing.")
        return 2

    print("Notification access: ALLOWED")
    print("Reading current Windows toast notifications...")

    try:
        notifications = await listener.get_notifications_async(NotificationKinds.TOAST)
        notifications = list(notifications)
    except Exception as exc:
        print("ERROR while reading notifications:")
        print(f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
        return 3

    print(f"Windows returned {len(notifications)} toast notification(s).")
    print()

    discord_found = False
    for index, notification in enumerate(notifications, start=1):
        try:
            app_name = notification.app_info.display_info.display_name
        except Exception:
            app_name = "<unknown app>"

        lines = _extract_text(notification)
        if "discord" in str(app_name).casefold() or any(
            "trendvision" in line.casefold() for line in lines
        ):
            discord_found = True
            print("=" * 72)
            print(f"MATCH #{index} - App: {app_name}")
            for line in lines:
                print(line)
            print("=" * 72)

    if not discord_found:
        print("No current Discord/TrendVision notification was found in the")
        print("Windows notification list. That is OK if no Discord toast is")
        print("currently retained by Windows. Run this probe again immediately")
        print("after a TrendVision popup appears.")
    else:
        print()
        print("SUCCESS: Windows Notification API can see Discord/TrendVision.")
        print("We can replace the UI Automation listener with this API.")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
