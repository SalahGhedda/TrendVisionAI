# TrendVisionAI

A local, read-only Windows listener for TrendVision scanner notifications from Discord.

## Current milestone: Phase 2 — Ticker memory

TrendVisionAI currently:

1. Reads Discord/TrendVision toasts through the Windows `UserNotificationListener` API.
2. Saves the exact WinRT payload locally for debugging and replay.
3. Detects the TrendVision channel.
4. Converts each notification into one or more channel-specific scanner events.
5. Stores those events in SQLite.
6. Accumulates events for the same ticker into durable ticker memory.
7. Tracks recent multi-channel convergence without making a trade decision yet.

There is **no OpenAI API call, market-data API, brokerage integration, or automatic trading** in this version.

## Why two storage layers?

`raw_notifications` / `winrt_candidates.jsonl` preserve what Windows actually exposed.

`scanner_events` stores the semantic event derived from that payload. Different TrendVision channels have different schemas, and `all-in-one-scanner` can produce multiple ticker events from one Discord notification.

`ticker_states` accumulates what the project has learned about each ticker across scanner events.

## Windows setup

### 1. Download/clone the project

Keep the project somewhere simple, for example:

```text
C:\Projects\TrendVisionAI
```

### 2. Install Python 3

Python 3.11 or 3.12 is recommended.

### 3. Run setup

Double-click:

```text
scripts\setup.bat
```

### 4. Start the Windows notification listener

Run:

```text
scripts\run_notification_api_listener.bat
```

Leave it open before the next TrendVision toast appears.

The listener saves candidates to:

```text
data\winrt_candidates.jsonl
```

and structured data to:

```text
data\trendvision.db
data\raw_notifications.jsonl
```

## Inspect captured notification formats

Run:

```text
scripts\inspect_candidates.bat
```

This groups representative WinRT payloads by TrendVision channel.

## Inspect ticker memory

List recently seen tickers:

```text
scripts\show_ticker_memory.bat
```

Inspect one ticker in detail:

```text
scripts\show_ticker_memory.bat LRHC
```

The ticker view shows:

- all stored event count
- all channels that have mentioned the ticker
- latest known non-empty facts from scanner payloads
- recent events inside a 30-minute convergence window

The convergence window is deliberately descriptive, not a trade score. For example, the system can now answer: "LRHC appeared in all-in-one, volume and squeeze scanners during the last 30 minutes."

## Channel handling

Channel-specific structures currently exist for:

- `all-in-one-scanner`
- `social-news`
- `news-scanner`
- `volume-scanner`
- `whale-scanner`
- `potential-squeeze-alerts`
- `0-borrow-scanner`
- `halt-scanner`
- `ipo-scanner`

Fields that Discord does not include in the Windows toast remain missing rather than being invented.

## Roadmap

- [x] Project scaffold
- [x] Windows notification API capture
- [x] Raw SQLite + JSONL storage
- [x] TrendVision channel detection
- [x] Channel-specific scanner events
- [x] Multi-item `all-in-one-scanner` handling
- [x] Duplicate protection
- [x] Per-ticker accumulated state
- [x] Recent multi-channel convergence view
- [ ] Tune significance/convergence rules using real collected alerts
- [ ] Decide which setups deserve deeper evaluation
- [ ] Add OpenAI evaluation only after the local filtering logic is useful
- [ ] Add final trade-candidate notification layer

## Safety

This project organizes and analyzes market information. It does not guarantee profitable trades, and it does not execute brokerage orders.
