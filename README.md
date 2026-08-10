# TrendVisionAI

TrendVisionAI is a local Windows decision-support dashboard that captures TrendVision Discord scanner notifications, builds per-ticker memory, tracks HIGH ATTENTION cases with Alpaca market data, evaluates screenshot-enhanced trade plans, and calibrates what actually happened afterward.

## Product goal

The practical end product is a **human-executed trade alert**. When the system has enough evidence, TrendVisionAI should alert the user with a candidate, entry zone, stop loss, targets, risk/reward and the evidence behind the alert. The application does **not** place brokerage orders.

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
Trade Plan v3 (user-supplied current chart)
        ↓
REJECT / WATCH / POTENTIAL TRADE
        ↓
If POTENTIAL TRADE:
Entry + Stop + T1 + T2 + R/R
        ↓
Objective trade-plan follow-up
        ↓
Trade Plan Statistics
        ↓
Experimental Candidate Qualification
```

The user remains the person who decides whether to trade.

## Setup / launch

```text
scripts\setup.bat
scripts\run_ui.bat
```

The UI starts its Windows notification listener and read-only market tracker automatically.

## Main capabilities

- Windows `UserNotificationListener` capture for TrendVision Discord toasts.
- Channel-specific scanner parsing and SQLite persistence.
- Durable ticker memory and recent multi-scanner convergence.
- Explainable Attention List; `HIGH ATTENTION` is review priority, not a buy signal.
- Manual AI Candidate Review v3.
- Alpaca read-only tracking for HIGH ATTENTION cases.
- Automatic 15m / 30m / 60m / 4h market-path outcomes.
- Detection-time Calibration Statistics by scanner combination, signal, RV, extension, market cap and risk conditions.
- Screenshot-enhanced Trade Plan v3.
- Experimental Entry / Stop / T1 / T2 persistence and objective follow-up.
- Trade Plan Statistics grouped by detection-time conditions and plan characteristics.
- Candidate Qualification v1 that remains evidence-locked until enough resolved plan history exists.

## Trade Plan v3

Ticker Memory lets the user paste or choose a current chart screenshot. OpenAI receives the underlying chart image plus stored TrendVision evidence and current usable Alpaca context.

Trade Plan v3 specifically:

- checks the age of the actual Alpaca trade, quote and minute-bar events separately from the API-poll timestamp;
- does not treat a freshly downloaded snapshot as proof that its latest trade is current;
- treats `IEX` as partial-venue data, not consolidated SIP/NBBO;
- treats legacy `zero_borrow=false` as UNKNOWN unless an explicit 0-borrow observation exists;
- ignores BUY / SELL / ENTRY / SL / TP recommendations printed by another tool inside the screenshot;
- does not directly compare differently defined TrendVision RV/volume with Alpaca IEX volume;
- blocks actionable plans when current market context is stale or otherwise unusable;
- blocks a fresh observed spread of 15%+ from producing `POTENTIAL TRADE` levels;
- keeps deterministic long-side coherence checks for Entry / Stop / T1 / T2.

The screenshot is used for underlying chart structure such as candles, wicks, breakout/pullback shape, consolidation, support/resistance and visible VWAP/EMA relationships. Exact current numerical market context comes from fresh usable Alpaca observations.

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

The **Candidate Qualification** page is the first implementation of the evidence gate that will eventually sit before the final trade alert.

It matches a current HIGH ATTENTION ticker's frozen detection-time conditions against resolved Trade Plan history and returns one of:

- `INSUFFICIENT EVIDENCE`
- `MONITOR`
- `MONITOR / RISK`
- `EXPERIMENTALLY QUALIFIED`

Current conservative readiness gates are intentionally hard-coded and transparent:

- at least **30 resolved entered Trade Plan cases globally** before qualification is enabled;
- at least **15 resolved cases** for a matched pattern before that pattern is mature enough to vote;
- an experimentally positive pattern currently requires T1 reached in at least 60% of resolved cases and stop-first no more than 30%;
- an experimentally negative pattern is flagged when T1 is 30% or lower or stop-first is 50% or higher;
- at least two **specific** mature positive matched patterns are required, with no mature specific negative match, before a ticker becomes `EXPERIMENTALLY QUALIFIED`;
- generic `ALL HIGH ATTENTION` evidence does not count as one of those positive patterns.

These thresholds are the initial transparent calibration framework. They are not claims that these values are statistically optimal. The qualification page does **not** generate Entry / Stop / Targets and does not send a final trade alert. An experimentally qualified ticker must still go through fresh chart + Trade Plan analysis.

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

## Storage

```text
data\trendvision.db
data\raw_notifications.jsonl
data\winrt_candidates.jsonl
data\trade_plan_images\...
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
- [ ] Collect enough real trade-plan cases for mature pattern evidence
- [ ] Tune/freeze qualification rules from accumulated evidence
- [ ] Automate the chart/trade-plan stage for qualified candidates
- [ ] Add final Windows/dashboard trade alert with Entry / Stop / T1 / T2 / R/R
- [ ] Add duplicate-alert cooldown and final alert blockers
- [ ] Validate frozen alert rules on new out-of-sample cases
- [ ] User manually decides and executes trades

## Safety / interpretation

TrendVisionAI is an experimental decision-support and calibration application. Attention tiers, AI reviews, chart interpretations, statistical patterns, qualification labels and trade-plan levels do not guarantee profitability. No brokerage order is placed by the application.
