# TrendVisionAI

TrendVisionAI is a local Windows dashboard that captures TrendVision Discord scanner notifications, turns them into structured events, accumulates per-ticker memory, and can manually request an AI second-pass review of an interesting ticker.

## Current milestone: Manual AI candidate review

The app currently:

1. Reads TrendVision Discord toasts through the Windows `UserNotificationListener` API.
2. Stores raw WinRT payloads for debugging/replay.
3. Parses channel-specific scanner events.
4. Stores everything in SQLite.
5. Builds durable ticker memory across multiple scanner channels.
6. Ranks recent scanner convergence into an **attention list** for review.
7. Presents the workflow through a PySide6 desktop UI.
8. Lets the user manually send one ticker's recent TrendVision case file to the OpenAI Responses API for a structured candidate review.

There is **no brokerage integration, automatic order execution, automatic AI scanning, or external market-data enrichment** in this version.

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
- latest saved AI candidate review

The **Analyze with AI** button is manual. It sends only the TrendVision information already stored for the ticker inside a 30-minute recent-convergence window. It does not add external price/chart/news/SEC data, and missing Discord-toast fields remain unknown rather than being invented.

AI reviews are saved in the local SQLite `ai_reviews` table so reopening the ticker shows the latest review.

### Listener & System

Shows whether the Windows notification listener is running, lets you start/stop it, and displays its diagnostic log.

This page also contains the OpenAI candidate-review settings. The API key is stored through the operating system credential store rather than in `config.json` or the Git repository. `OPENAI_API_KEY` is also supported when set in the environment.

The default review model is `gpt-5-mini`; the model field is editable in the UI.

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
- `ai_reviews` — manually requested AI reviews plus the exact local case-file snapshot used for each review

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
- [x] Manual AI candidate review
- [ ] Tune attention + AI review logic from real observed outcomes
- [ ] Decide which AI-reviewed setups deserve a user-facing candidate alert
- [ ] Optionally automate AI review only for high-quality local candidates
- [ ] Add trade-candidate notification layer

## Safety

TrendVisionAI organizes and prioritizes market information. AI reviews are based only on the supplied scanner data, do not guarantee profitable trades, and do not place brokerage orders.
