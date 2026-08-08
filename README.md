# TrendVisionAI

A local, read-only Windows listener for TrendVision scanner notifications from Discord.

## Current milestone: Phase 1 — Notification capture

The goal of v0.1 is intentionally small:

1. Watch the Windows accessibility/UI Automation tree for a visible Discord toast containing `TrendVision`.
2. Extract whatever text Windows exposes (server/channel/message).
3. Save the raw alert locally in SQLite and JSONL.
4. Print the alert in the terminal so we can verify exactly what Windows gives us.

There is **no OpenAI API call, market-data API, brokerage integration, or automatic trading** in this version.

## Why this approach

This prototype does not log in to Discord programmatically and does not use a Discord user token or self-bot. It only reads text that Windows already exposes locally through UI Automation while a notification is visible.

## Windows setup

### 1. Download/clone the project

Keep the project somewhere simple, for example:

```text
C:\Projects\TrendVisionAI
```

### 2. Install Python 3

Python 3.11 or 3.12 is recommended for the prototype.

### 3. Run setup

Double-click:

```text
scripts\setup.bat
```

This creates `.venv`, installs `pywinauto`, and creates `config.json`.

### 4. Start the listener

Double-click:

```text
scripts\run_listener.bat
```

You should see:

```text
TrendVisionAI Listener v0.1
Listening for visible Windows notifications containing 'TrendVision'...
Leave this window open. Press Ctrl+C to stop.
```

Now minimize Discord and wait for a TrendVision notification.

A successful capture should look similar to:

```text
========================================================================
[2026-08-08T12:15:04-04:00] TRENDVISION ALERT [SAVED]
Channel : #volume-scanner
Ticker  : XHLD
Title   : XHLD - Alert #6 | +6.41%
Body:
Price $2.99 MCap 33.66M Float 7.79M
Rel Vol 10.33x
========================================================================
```

## Where alerts are stored

```text
data\trendvision.db
data\raw_notifications.jsonl
```

The database already gives us the foundation for the next phase, where alerts for the same ticker will be accumulated rather than treated as independent events.

## If the listener does not capture the next notification

Run:

```text
scripts\run_listener_debug.bat
```

Then send the terminal output produced while a TrendVision notification is on screen. The UI Automation structure differs between Windows builds, so the first live test is expected to tell us what selector needs tightening.

## Roadmap

- [x] Project scaffold
- [x] Raw SQLite notification storage
- [x] TrendVision channel parser
- [x] Duplicate protection
- [x] Windows UI Automation prototype
- [ ] Verify capture against a real TrendVision Windows toast
- [ ] Parse rich scanner alert fields (price, float, RVOL, CTB, etc.)
- [ ] Build per-ticker accumulated state
- [ ] Add significance rules so trivial updates do not call AI
- [ ] Add OpenAI evaluation
- [ ] Add live market/news verification
- [ ] Add high-quality trade-candidate notification layer

## Safety

This project is intended to help organize and analyze market information. It does not guarantee profitable trades. The first versions deliberately do not execute brokerage orders.
