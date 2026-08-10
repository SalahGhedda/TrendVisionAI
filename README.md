# TrendVisionAI

TrendVisionAI is a local Windows dashboard that captures TrendVision Discord scanner notifications, turns them into structured events, accumulates per-ticker memory, tracks HIGH ATTENTION cases with Alpaca market data, and calibrates what actually happened afterward.

## Current milestone: screenshot-enhanced trade-plan experiments

The practical product goal is a **human-executed trade alert**: when sufficiently good conditions are eventually proven, TrendVisionAI should be able to alert the user with a candidate, entry zone, stop loss, targets, risk/reward, and the evidence behind the alert. The application does **not** place brokerage orders.

The app currently:

1. Reads TrendVision Discord toasts through the Windows `UserNotificationListener` API.
2. Stores raw WinRT payloads for debugging/replay.
3. Parses channel-specific scanner events.
4. Stores everything in SQLite.
5. Builds durable ticker memory across multiple scanner channels.
6. Ranks recent scanner convergence into an **attention list** for review.
7. Lets the user manually request a structured OpenAI candidate review.
8. Automatically starts Alpaca market tracking when a ticker reaches local `HIGH ATTENTION`.
9. Measures reference price, return, MFE, MAE, spread and volume observations for up to four hours.
10. Automatically classifies completed 15m / 30m / 60m / 4h market paths.
11. Freezes detection-time TrendVision conditions and builds aggregate calibration statistics.
12. Lets the user paste or choose a **current chart screenshot** for a HIGH ATTENTION ticker.
13. Sends the screenshot together with stored TrendVision evidence and latest Alpaca measurements to the OpenAI Responses API for an experimental multimodal trade-plan review.
14. Uses Trade Plan v2 freshness/feed-scope guardrails so stale or delayed market observations cannot produce actionable levels.
15. Saves potential entry / stop / T1 / T2 plans only when deterministic long-plan guardrails pass.
16. Automatically follows each saved plan with Alpaca samples to measure whether the proposed entry was observed and whether stop / T1 / T2 were subsequently observed.

There is **no brokerage integration or automatic order execution**. Alpaca is read-only market data; TrendVision remains discovery; the user remains the person who decides whether to trade.

## Setup

Run once after cloning, and again only when dependencies change:

```text
scripts\setup.bat
```

## Start TrendVisionAI

```text
scripts\run_ui.bat
```

The desktop app starts its Windows notification listener and market tracker automatically.

## Core workflow

```text
TrendVision Discord alerts
        ↓
Ticker memory + scanner convergence
        ↓
Attention ranking
        ↓
HIGH ATTENTION
        ↓
Alpaca read-only market tracking
        ↓
Automatic 15m / 30m / 60m / 4h outcomes
        ↓
Calibration Statistics
```

The optional trade-plan experiment runs in parallel:

```text
HIGH ATTENTION ticker
        ↓
User pastes current chart screenshot
        ↓
TrendVision evidence
+ fresh Alpaca measurements
+ chart screenshot
        ↓
OpenAI multimodal review
        ↓
REJECT / WATCH / POTENTIAL TRADE
        ↓
If POTENTIAL TRADE:
Entry zone + Stop + T1 + T2 + R/R + invalidation
        ↓
Save plan
        ↓
Objective Alpaca follow-up
```

The screenshot experiment does **not** replace the existing calibration pipeline. Both continue at the same time.

## UI screens

### Dashboard

Shows the recent Attention List with ticker, attention tier, score, events, scanner channels and explanation/risk flags. Attention is review priority, not a guaranteed trade signal.

### Live Alerts

Shows structured TrendVision events. Double-click a ticker to open Ticker Memory.

### Ticker Memory

Shows accumulated facts and the scanner timeline for one ticker.

It also contains:

- **AI Candidate Review v3** — manual TrendVision-only second-pass review.
- **Automatic Outcome Calibration** — objective post-review 15m / 30m / 60m / 4h behavior.
- **Trade Plan Experiment v2** — paste or choose the current chart screenshot, then analyze the current case with TrendVision + Alpaca + chart vision.

For Trade Plan Experiment v2, a `POTENTIAL TRADE` can contain:

- entry zone
- stop loss
- target 1
- target 2
- conservative risk/reward based on the top of the entry zone
- entry trigger
- invalidation
- chart observations
- positive/risk factors
- what still needs confirmation

V2 adds calibration rules learned from the first real screenshot test:

- the latest Alpaca sample must be no more than **45 seconds old** before the OpenAI trade-plan request is allowed;
- delayed SIP is never treated as a current quote for actionable levels;
- `IEX` is explicitly treated as **partial-venue data**, not consolidated SIP/NBBO;
- different prices at different timestamps are normal time-series observations, not automatic conflicts;
- different RV values are not called contradictory unless their calculation windows/baselines are known to be directly comparable;
- small float/market cap can increase volatility, slippage and liquidity sensitivity but does not by itself prove manipulation or a pump;
- one IEX quote/size observation is not treated as full-market depth;
- `what to confirm` is constrained to observations TrendVisionAI can actually obtain from subsequent Alpaca samples, supported TrendVision alerts, or a newer chart screenshot.

The existing deterministic long-plan checks also remain: incoherent price levels, EXTREME risk, dangerous/unclear chart structure, multiple recent halts, or a 100%+ recent move cannot be promoted to a clean `POTENTIAL TRADE` without further confirmation.

Chart vision is used for qualitative structure. Exact numeric market values come from fresh stored Alpaca observations supplied to the request.

### Calibration Journal

Compares saved AI candidate reviews against automatic objective outcomes.

### Market Tracking

When a ticker reaches `HIGH ATTENTION`, TrendVisionAI creates a read-only Alpaca tracking session for up to four hours and polls snapshots every 15 seconds.

Stored observations include:

- latest trade price / size
- bid / ask and sizes
- spread / spread percentage
- minute OHLCV
- minute VWAP when supplied
- daily volume when supplied
- raw snapshot JSON

The first successful sample becomes the tracking reference. The page calculates return, MFE, MAE, peak/trough timing, volume/spread metrics and automatic horizon outcomes.

The default free `IEX` feed is not consolidated SIP data.

### Calibration Statistics

Freezes a 30-minute pre-trigger TrendVision feature snapshot for each HIGH ATTENTION tracking session and compares detection-time conditions with later objective outcomes.

Examples of grouped conditions include:

- attention-score bucket
- number of scanner channels
- exact channel combinations
- BREAKOUT / MOMENTUM and other observed signal labels
- relative-volume bucket
- extension bucket
- market-cap bucket when available
- zero borrow observation
- whale direction
- pre-trigger halt observation
- compound patterns such as `BREAKOUT + RV>=10x`

Statistics include sample count, median return, median MFE, median MAE, up-continuation rate, spike/reversal rate and negative-outcome rate.

Sample maturity:

- `<5` — `TOO EARLY`
- `5-14` — `EARLY`
- `15-29` — `BUILDING`
- `30+` — `MORE STABLE`

These labels are not statistical-significance claims and do not automatically change the attention algorithm.

### Trade Plan Experiments

Shows all saved screenshot-enhanced trade plans and their objective follow-up.

For plans that passed `POTENTIAL TRADE`, the evaluator watches stored post-plan Alpaca sampled trade prices and records descriptive statuses such as:

- `WAITING FOR ENTRY`
- `ENTRY NOT REACHED`
- `OPEN / IN PROGRESS`
- `TARGET 1 HIT / OPEN`
- `TARGET 1 ONLY`
- `TARGET 2 HIT`
- `STOP HIT FIRST`
- `TARGET 1 THEN STOP`

It also stores observed entry time/price and post-entry max return / max drawdown. This measures the proposed plan; it does not claim that the user personally entered the trade.

### Listener & System

Contains listener controls plus OpenAI and Alpaca configuration. API credentials are stored through Windows Credential Manager rather than in the repository.

## Storage

```text
data\trendvision.db
data\raw_notifications.jsonl
data\winrt_candidates.jsonl
data\trade_plan_images\...
```

`data/*` is gitignored.

Main SQLite layers:

- `raw_notifications`
- `scanner_events`
- `ticker_states`
- `ai_reviews`
- `ai_review_outcomes` — legacy/manual history
- `market_tracking_sessions`
- `market_samples`
- `automatic_outcomes`
- `calibration_feature_snapshots`
- `trade_plans` — saved screenshot-enhanced AI plans and exact request snapshot
- `trade_plan_evaluations` — objective sampled-price follow-up for saved plans

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

## Roadmap

- [x] Windows notification capture
- [x] Channel-specific parsing
- [x] SQLite / JSONL persistence
- [x] Ticker memory and scanner convergence
- [x] Explainable attention ranking
- [x] Desktop dashboard
- [x] AI candidate review v3
- [x] Alpaca read-only HIGH ATTENTION tracking
- [x] Objective return / MFE / MAE measurements
- [x] Automatic 15m / 30m / 60m / 4h outcome classification
- [x] Aggregate calibration statistics
- [x] Manual chart screenshot input
- [x] Multimodal TrendVision + Alpaca + chart Trade Plan Experiment v1
- [x] Trade Plan v2 freshness/feed-scope/reasoning guardrails
- [x] Persist entry / stop / T1 / T2 experiments
- [x] Objective post-plan sampled-price evaluation
- [ ] Collect enough real HIGH ATTENTION and trade-plan cases for meaningful calibration
- [ ] Backfill exact historical 1-minute bars for more precise outcome/trade-plan evaluation
- [ ] Compare scanner-only vs scanner+market vs scanner+market+chart usefulness
- [ ] Tune qualification rules only after enough evidence exists
- [ ] Build Candidate Qualification Engine
- [ ] Turn qualified, validated plans into user-facing real-time trade alerts
- [ ] User manually decides and executes trades

## Safety / interpretation

TrendVisionAI is an experimental decision-support and calibration application. Attention tiers, AI reviews, chart interpretations, statistical patterns and trade-plan levels do not guarantee profitability. The current trade-plan output is being measured precisely because it is **not yet considered a proven alert system**. No brokerage order is placed by the application.