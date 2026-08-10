# TrendVisionAI

TrendVisionAI is a local Windows decision-support dashboard that captures TrendVision Discord scanner notifications, builds per-ticker memory, tracks HIGH ATTENTION cases with Alpaca market data, evaluates trade plans, and calibrates what actually happened afterward.

## Product goal

The practical end product is a **human-executed trade alert**. When the system has enough evidence and the current setup passes all live checks, TrendVisionAI should alert the user with a candidate, entry zone, stop loss, targets, risk/reward and the evidence behind the alert. The application does **not** place brokerage orders.

The system learns **setup conditions across many tickers**, not whether one particular stock symbol was historically good.

## Current workflow

```text
TrendVision Discord alerts
        ↓
Ticker memory + scanner convergence
        ↓
HIGH ATTENTION
        ↓
Read-only Alpaca tracking
        ↓
Automatic market outcomes + Calibration Statistics
        ↓
Trade Plan v3 experiments
        ↓
Objective Entry / Stop / T1 / T2 follow-up
        ↓
Trade Plan Statistics
        ↓
Candidate Qualification
        ↓
Automatic live pipeline
```

During regular US market hours, the automatic live pipeline can now run:

```text
HIGH ATTENTION
      ↓
Automatic qualification check
      ↓
Automatic Alpaca 1-minute chart context
      ↓
Automatic Trade Plan v3
      ↓
POTENTIAL TRADE?
      ↓
Deterministic hard alert gate
      ↓
🚨 Windows/dashboard Trade Alert
      ↓
User manually decides whether to trade
```

Before qualification evidence is mature, the same automatic chart + Trade Plan path can run in **calibration-only mode** so the dataset continues growing without generating a user-facing trade alert.

## Setup / launch

```text
scripts\setup.bat
scripts\run_ui.bat
```

The UI starts its Windows notification listener, read-only market tracker and live pipeline automatically.

## Main capabilities

- Windows `UserNotificationListener` capture for TrendVision Discord toasts.
- Channel-specific scanner parsing and SQLite persistence.
- Durable ticker memory and recent multi-scanner convergence.
- Explainable Attention List; `HIGH ATTENTION` is review priority, not a buy signal.
- Manual AI Candidate Review v3.
- Alpaca read-only tracking for HIGH ATTENTION cases.
- Automatic 15m / 30m / 60m / 4h market-path outcomes.
- Detection-time Calibration Statistics by scanner combination, signal, RV, extension, market cap and risk conditions.
- Manual screenshot-enhanced Trade Plan v3.
- Experimental Entry / Stop / T1 / T2 persistence and objective follow-up.
- Trade Plan Statistics grouped by detection-time conditions and plan characteristics.
- Candidate Qualification v1 that remains evidence-locked until enough resolved plan history exists.
- Automatic recent Alpaca 1-minute chart acquisition/rendering for live calibration and qualified candidates.
- Automatic Trade Plan v3 execution for new HIGH ATTENTION tracking sessions during regular hours.
- Qualified-candidate Windows notifications with persistent deduplication.
- Final deterministic alert gate and Windows/dashboard trade alerts when a qualified POTENTIAL TRADE passes all blockers.

## Trade Plan v3

Ticker Memory still lets the user paste or choose a current chart screenshot manually. OpenAI receives the underlying chart image plus stored TrendVision evidence and current usable Alpaca context.

Trade Plan v3 specifically:

- checks the age of the actual Alpaca trade, quote and minute-bar events separately from the API-poll timestamp;
- does not treat a freshly downloaded snapshot as proof that its latest trade is current;
- treats `IEX` as partial-venue data, not consolidated SIP/NBBO;
- treats legacy `zero_borrow=false` as UNKNOWN unless an explicit 0-borrow observation exists;
- ignores BUY / SELL / ENTRY / SL / TP recommendations printed by another tool inside a manual screenshot;
- does not directly compare differently defined TrendVision RV/volume with Alpaca IEX volume;
- blocks actionable plans when current market context is stale or otherwise unusable;
- blocks a fresh observed spread of 15%+ from producing `POTENTIAL TRADE` levels;
- keeps deterministic long-side coherence checks for Entry / Stop / T1 / T2.

## Automatic chart context

For the live pipeline, TrendVisionAI requests recent Alpaca **1-minute bars** for the ticker and renders its own neutral candlestick/volume image under `data/auto_trade_charts/`.

The generated chart:

- contains no BUY / SELL / SL / TP recommendations;
- is paired with the same structured bar list sent to the model;
- is used for trend, candle, wick, pullback, consolidation and support/resistance structure;
- inherits the limitations of the configured Alpaca feed. With the default free `IEX` feed it remains partial-venue evidence.

Exact current planning price/quote still comes from fresh Trade Plan v3 market context rather than from pixels in the generated chart.

## Calibration Statistics

For each HIGH ATTENTION tracking session, TrendVisionAI freezes a 30-minute pre-trigger feature snapshot so future scanner events cannot leak into the detection-time evidence.

Examples include:

- attention score bucket;
- scanner count and exact channel combination;
- BREAKOUT / MOMENTUM and other explicit signals;
- relative-volume bucket;
- extension bucket;
- market-cap bucket when available;
- zero-borrow observation;
- whale direction;
- pre-trigger halt observation;
- compound conditions such as `BREAKOUT + RV>=10x`.

The page compares those conditions with objective 15m / 30m / 60m / 4h outcomes.

## Trade Plan Statistics

The **Trade Plan Statistics** page measures the actual experimental plans rather than only asking whether the stock moved up after HIGH ATTENTION.

For each pattern it shows:

- actionable plan count;
- entry-observed count;
- resolved post-entry count;
- entry-reached rate;
- T1-reached rate;
- T2-reached rate;
- stop-first rate;
- median post-entry MFE;
- median post-entry MAE;
- sample-maturity label.

T1/T2/stop rates use resolved post-entry cases. Open plans are excluded from those denominators.

Sample maturity remains:

- `<5` — `TOO EARLY`
- `5-14` — `EARLY`
- `15-29` — `BUILDING`
- `30+` — `MORE STABLE`

These are evidence-maturity labels, not guaranteed win rates, statistical proof or expected profit.

## Candidate Qualification v1

Candidate Qualification matches a current HIGH ATTENTION ticker's frozen detection-time conditions against resolved Trade Plan history and returns one of:

- `INSUFFICIENT EVIDENCE`
- `MONITOR`
- `MONITOR / RISK`
- `EXPERIMENTALLY QUALIFIED`

Current transparent readiness gates:

- at least **30 resolved entered Trade Plan cases globally** before qualification is enabled;
- at least **15 resolved cases** for a matched pattern before that pattern is mature enough to vote;
- an experimentally positive pattern currently requires T1 reached in at least 60% of resolved cases and stop-first no more than 30%;
- an experimentally negative pattern is flagged when T1 is 30% or lower or stop-first is 50% or higher;
- at least two **specific** mature positive matched patterns are required, with no mature specific negative match, before a ticker becomes `EXPERIMENTALLY QUALIFIED`;
- generic `ALL HIGH ATTENTION` evidence does not count as one of those positive patterns.

These thresholds are an initial transparent calibration framework, not claims that the values are statistically optimal.

## Live Trade Pipeline

The **Live Trade Pipeline** page records the automatic workflow and its deduplicated events.

A HIGH ATTENTION session can receive one automatic calibration Trade Plan during the configured regular session when fresh Alpaca context and credentials are available. This helps the Trade Plan dataset grow even while Candidate Qualification is still evidence-locked.

When a ticker is `EXPERIMENTALLY QUALIFIED`, TrendVisionAI also records/notifies the qualified candidate. If its automatic Trade Plan returns `POTENTIAL TRADE`, the final alert gate requires all of the following before a trade alert can be emitted:

- regular US equity session (configured 09:30-16:00 America/New_York);
- historical status = `EXPERIMENTALLY QUALIFIED`;
- Trade Plan decision = `POTENTIAL TRADE`;
- fresh usable market context;
- a fresh quote;
- known observed spread below the configured 15% guardrail;
- coherent Entry / Stop / T1 / T2;
- no `EXTREME` plan risk;
- chart structure not `DANGEROUS` / `UNCLEAR`;
- no multiple recent halt observations;
- no previous final alert for the same tracking session.

The local clock gate is intentionally simple; stale/unusable market data remains an independent blocker when the exchange is not actually producing current data.

A final notification contains Entry, Stop, T1 and T2 and is explicitly for **manual user decision/execution only**.

## UI screens

- Dashboard
- Live Alerts
- Ticker Memory
- Listener & System
- Calibration Journal
- Market Tracking
- Calibration Statistics
- Trade Plan Experiments
- Trade Plan Statistics
- Candidate Qualification
- Live Trade Pipeline

## Storage

```text
data\trendvision.db
data\raw_notifications.jsonl
data\winrt_candidates.jsonl
data\trade_plan_images\...
data\auto_trade_charts\...
```

Important SQLite layers include:

- `raw_notifications`
- `scanner_events`
- `ticker_states`
- `ai_reviews`
- `market_tracking_sessions`
- `market_samples`
- `automatic_outcomes`
- `calibration_feature_snapshots`
- `trade_plans`
- `trade_plan_evaluations`
- `live_pipeline_events`

`data/*` is gitignored. API credentials are stored through Windows Credential Manager rather than in the repository.

## Roadmap

- [x] Windows notification capture
- [x] Channel-specific parsing
- [x] SQLite / JSONL persistence
- [x] Ticker memory and convergence
- [x] Explainable attention ranking
- [x] Desktop dashboard
- [x] AI Candidate Review v3
- [x] Alpaca read-only HIGH ATTENTION tracking
- [x] Objective 15m / 30m / 60m / 4h outcomes
- [x] Calibration Statistics
- [x] Manual chart screenshot input
- [x] Trade Plan v3
- [x] Persist Entry / Stop / T1 / T2 experiments
- [x] Objective post-plan evaluation
- [x] Trade Plan Statistics
- [x] Candidate Qualification v1 framework / readiness gate
- [x] Automatic qualification checks in the live pipeline
- [x] Qualified-candidate notification + persistent deduplication
- [x] Automatic Alpaca 1-minute chart context
- [x] Automatic Trade Plan stage
- [x] Deterministic final alert blockers
- [x] Windows/dashboard Entry / Stop / T1 / T2 trade alert path
- [ ] Collect enough real trade-plan cases for mature pattern evidence
- [ ] Tune/freeze qualification rules from accumulated evidence
- [ ] Validate frozen alert rules on new out-of-sample cases
- [ ] User manually decides and executes trades

## Safety / interpretation

TrendVisionAI is an experimental decision-support and calibration application. Attention tiers, AI reviews, chart interpretations, statistical patterns, qualification labels and trade-plan levels do not guarantee profitability. No brokerage order is placed by the application.
