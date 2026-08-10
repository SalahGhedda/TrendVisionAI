# TrendVisionAI

TrendVisionAI is a local Windows dashboard that captures TrendVision Discord scanner notifications, turns them into structured events, accumulates per-ticker memory, and can manually request an AI second-pass review of an interesting ticker.

## Current milestone: high-attention market tracking

The app currently:

1. Reads TrendVision Discord toasts through the Windows `UserNotificationListener` API.
2. Stores raw WinRT payloads for debugging/replay.
3. Parses channel-specific scanner events.
4. Stores everything in SQLite.
5. Builds durable ticker memory across multiple scanner channels.
6. Ranks recent scanner convergence into an **attention list** for review.
7. Presents the workflow through a PySide6 desktop UI.
8. Lets the user manually send one ticker's recent TrendVision case file to the OpenAI Responses API for a structured candidate review.
9. Applies deterministic local guardrails after the model response.
10. Stores user-labeled post-review outcomes for calibration.
11. Automatically starts an Alpaca market-data tracking session when a ticker reaches local `HIGH ATTENTION`, then measures what happens afterward for up to four hours.

There is **no brokerage integration, automatic order execution, or automatic AI scanning** in this version. Alpaca is used as a read-only market-data source; TrendVision remains the discovery source.

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

The desktop app automatically starts and stops its own Windows notification listener and market-data tracking controller.

## UI screens

### Dashboard

Shows the recent **Attention List** with ticker, attention tier, score, recent events, independent scanner channels, contributing scanner types, and explanation/risk flags.

The ranking is a review-priority tool, **not a buy/sell signal**.

### Live Alerts

A live table of structured TrendVision scanner events. Filter by ticker, text, or scanner channel. Double-click a ticker to open its memory.

### Ticker Memory

Shows everything accumulated for one ticker: first/last appearance, total events, independent channels, latest known TrendVision facts, full activity timeline, latest saved AI candidate review, and the review-outcome journal.

The **Analyze with AI** button is manual. It sends only the TrendVision information already stored for the ticker inside a 30-minute recent-convergence window. It does not browse or add external chart/news/SEC/options data to the AI case file, and missing Discord-toast fields remain unknown rather than being invented.

AI Review v3 separates four questions:

- **Interest** — how unusual / worthy of attention the scanner convergence is (`LOW` to `VERY HIGH`)
- **Risk** — how dangerous, extended, halted, volatile or chase-prone it is (`LOW` to `EXTREME`)
- **Evidence quality** — how complete and internally consistent the captured evidence is (`LOW` to `HIGH`)
- **Review status** — `IGNORE`, `WATCH`, `WAIT FOR CONFIRMATION`, `POTENTIAL SETUP`, or `AVOID`

V3 treats different prices/change percentages at different timestamps as normal time-series updates rather than contradictions, treats HALTED UP/HALTED DOWN events at different times as chronology rather than data conflict, normalizes legacy unobserved `zero_borrow=false` values to unknown, and filters unsupported future-signal suggestions.

### Review Outcome Journal

Each reviewed ticker has an outcome section. After enough time has passed, label what actually happened using one of:

- `STRONG CONTINUATION`
- `MODEST CONTINUATION`
- `FAILED / REVERSED`
- `NO CLEAN SETUP`
- `TOO RISKY / UNTRADEABLE`
- `NOT ENOUGH FOLLOW-UP`

Choose a review horizon (15 min, 30 min, 60 min, or 4 hours) and optionally add notes. TrendVisionAI also records how many later scanner events/channels appeared during that horizon.

The sidebar **Calibration Journal** page lists saved AI reviews and which ones have or have not been outcome-labeled.

### Market Tracking

This page is the objective measurement layer.

When the local attention engine marks a ticker `HIGH ATTENTION`, TrendVisionAI automatically creates a read-only market tracking session for up to 4 hours. It polls Alpaca's multi-symbol stock snapshot endpoint every 15 seconds and stores:

- latest trade price / size
- latest bid / ask and sizes
- bid/ask spread and spread percentage
- latest minute OHLCV bar
- minute VWAP when supplied by Alpaca
- daily volume when supplied by Alpaca
- exact raw snapshot JSON for later debugging

The first successful Alpaca snapshot after `HIGH ATTENTION` becomes the session's **reference price**. The page then calculates descriptive measurements such as:

- return from reference price
- **MFE** (maximum favorable excursion) from stored minute highs
- **MAE** (maximum adverse excursion) from stored minute lows
- sample count and current feed/status

This is intentionally asymmetric:

```text
TrendVision -> discovery / scanner convergence
Alpaca      -> objective market measurement after HIGH ATTENTION
AI          -> manual interpretation of the TrendVision case
```

The first implementation uses Alpaca REST snapshots rather than brokerage endpoints and never places an order.

### Listener & System

Shows whether the Windows notification listener is running, lets you start/stop it, and displays its diagnostic log.

This page contains both OpenAI review settings and Alpaca market-data settings. API secrets are stored through Windows Credential Manager rather than in `config.json` or the Git repository.

For Alpaca, save both the **API key ID** and **secret key**, then choose a feed:

- `IEX` — default; appropriate for the initial Basic/free prototype
- `SIP` — consolidated US-exchange feed when the Alpaca subscription permits it
- `Delayed SIP` — consolidated data with delay

Changing the feed does not alter existing stored samples; each session/sample records which feed produced it.

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
- `ai_review_outcomes` — manual outcome labels and post-review scanner follow-up counts
- `market_tracking_sessions` — HIGH ATTENTION market-measurement sessions
- `market_samples` — Alpaca snapshots collected during those sessions

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

The old scripts such as `show_ticker_memory.bat`, `show_attention_list.bat`, and `inspect_candidates.bat` remain useful for debugging, but normal use should happen through `run_ui.bat`.

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
- [x] AI review v3 calibration + supported-feed constraints
- [x] Local review outcome journal
- [x] Alpaca read-only HIGH ATTENTION market tracking
- [x] Objective reference-price / return / MFE / MAE measurements
- [ ] Collect real tracked candidates and compare market outcomes to attention/AI labels
- [ ] Backfill exact 1-minute historical bars at session completion for more precise outcome metrics
- [ ] Tune attention + AI review logic from observed outcomes
- [ ] Decide which reviewed setups deserve a user-facing candidate alert
- [ ] Optionally automate AI review only for high-quality local candidates
- [ ] Add trade-candidate notification layer

## Safety

TrendVisionAI organizes, measures, and prioritizes market information. AI reviews and attention levels do not guarantee profitable trades. The application does not place brokerage orders.
