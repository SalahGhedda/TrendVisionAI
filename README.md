# TrendVisionAI

TrendVisionAI is a local Windows dashboard that captures TrendVision Discord scanner notifications, turns them into structured events, and accumulates per-ticker memory.

## Current milestone: Desktop dashboard

The app currently:

1. Reads TrendVision Discord toasts through the Windows `UserNotificationListener` API.
2. Stores raw WinRT payloads for debugging/replay.
3. Parses channel-specific scanner events.
4. Stores everything in SQLite.
5. Builds durable ticker memory across multiple scanner channels.
6. Ranks recent scanner convergence into an **attention list** for review.
7. Presents the workflow through a PySide6 desktop UI.

There is **no brokerage integration, automatic order execution, OpenAI evaluation, or external market-data enrichment** in this version.

## Setup

Run once after cloning, and again whenever dependencies change:

```text
scripts\setup.bat
```

## Start TrendVisionAI

Stop any old terminal listener first, then launch:

```text
scripts\run_ui.bat
```

The desktop app automatically starts and stops its own Windows notification listener.

## UI screens

### Dashboard

Shows the recent **Attention List** with:

- ticker
- attention tier
- score
- recent event count
- number of independent scanner channels
- contributing scanner types
- explanation / risk flags

The ranking is a review-priority tool, **not a buy/sell signal**.

### Live Alerts

A live table of structured TrendVision scanner events. Filter by ticker, text, or scanner channel. Double-click a ticker to open its memory.

### Ticker Memory

Shows everything accumulated for one ticker:

- first/last appearance
- total events
- independent channels
- latest known facts supplied by TrendVision
- full activity timeline

Missing Discord-toast fields stay missing rather than being invented.

### Listener & System

Shows whether the Windows notification listener is running, lets you start/stop it, and displays its diagnostic log.

## Storage

```text
data\trendvision.db
data\raw_notifications.jsonl
data\winrt_candidates.jsonl
```

Main SQLite layers:

- `raw_notifications` — normalized notifications captured from Windows
- `scanner_events` — channel-specific structured events
- `ticker_states` — durable accumulated state for each ticker

`all-in-one-scanner` can create multiple ticker events from a single Discord notification.

## Supported TrendVision channels

- `all-in-one-scanner`
- `social-news`
- `news-scanner`
- `volume-scanner`
- `whale-scanner`
- `potential-squeeze-alerts`
- `0-borrow-scanner`
- `halt-scanner`
- `ipo-scanner`

## Legacy development commands

The old scripts such as `show_ticker_memory.bat`, `show_attention_list.bat`, and `inspect_candidates.bat` remain useful for debugging, but normal use should now happen through `run_ui.bat`.

## Roadmap

- [x] Windows notification API capture
- [x] Channel-specific scanner parsing
- [x] SQLite / JSONL persistence
- [x] Multi-item all-in-one handling
- [x] Ticker memory
- [x] Recent convergence logic
- [x] Explainable attention ranking
- [x] Desktop dashboard
- [ ] Tune attention rules from real outcomes
- [ ] Decide when a setup deserves deeper analysis
- [ ] Add AI evaluation only after local filtering is reliable
- [ ] Add user-facing trade-candidate alerts

## Safety

TrendVisionAI organizes and prioritizes market information. It does not guarantee profitable trades and does not place brokerage orders.
