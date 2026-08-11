# TrendVisionAI

TrendVisionAI is a local Windows decision-support dashboard that captures TrendVision Discord scanner notifications, builds per-ticker memory, tracks HIGH ATTENTION cases with Alpaca market data, recognizes configured intraday setups, evaluates trade plans, and calibrates what happened afterward.

## Product goal

The practical end product is a **human-executed trade alert**. TrendVisionAI can surface a candidate with an entry zone, stop loss, targets, risk/reward, recognized setup and supporting/risk evidence. The application does **not** place brokerage orders.

The system learns **setup conditions across many tickers**, not whether one particular stock symbol was historically good.

## Strategy architecture

TrendVisionAI no longer asks its own small dataset to invent the trading strategy from scratch.

```text
Known Strategy Library
        +
TrendVision scanner evidence
        +
Current Alpaca market/chart context
        ↓
Recognized setup instance
        ↓
AI Trade Plan inside that setup framework
        ↓
Historical TrendVisionAI calibration validates / filters it
        ↓
Deterministic hard alert gate
        ↓
🚨 Manual-decision Trade Alert
```

Historical calibration remains useful, but its role is now **validator/filter**, not strategy inventor. Immature history alone does not erase a recognized setup. Mature negative strategy/condition evidence can veto a final alert.

## Current automatic workflow

```text
TrendVision Discord alerts
        ↓
Ticker memory + scanner convergence
        ↓
HIGH ATTENTION
        ↓
Read-only Alpaca tracking
        ↓
Strategy Library scans current regular-session 1-minute bars
        ↓
NO VALID SETUP → wait and scan again later
        │
        └── Known setup recognized
                 ↓
        Automatic neutral chart context
                 ↓
        Automatic Trade Plan v3
                 ↓
        Calibration Validator
                 ↓
        Deterministic hard alert gate
                 ↓
        🚨 Windows/dashboard Trade Alert
                 ↓
        User manually decides whether to trade
```

No OpenAI Trade Plan request is used when no configured strategy setup is recognized.

## Strategy Library v1

The Strategy Library currently contains transparent deterministic implementations of these common intraday setup families:

- **5-Min Opening Range Breakout (`ORB_5M`)**
- **15-Min Opening Range Breakout (`ORB_15M`)**
- **Breakout + Retest (`BREAKOUT_RETEST`)**
- **High-of-Day Breakout (`HOD_BREAKOUT`)**
- **First Pullback After Momentum (`FIRST_PULLBACK`)**
- **Session VWAP Reclaim + Hold (`VWAP_RECLAIM_HOLD`)**

These implementations are explicit TrendVisionAI rules, not claims that any strategy is universally profitable. Examples of the deterministic checks include fresh level breaks, controlled retests, anti-chase extension limits, same-feed relative bar-volume comparisons, pullback retracement structure and a session cumulative VWAP calculated from the supplied Alpaca bars.

The Strategy Library page shows the configured setups and recent recognized instances.

## Setup / launch

```text
scripts\setup.bat
scripts\run_ui.bat
```

The UI starts its Windows notification listener, read-only market tracker and strategy-aware live pipeline automatically.

## Main capabilities

- Windows `UserNotificationListener` capture for TrendVision Discord toasts.
- Channel-specific scanner parsing and SQLite persistence.
- Durable ticker memory and recent multi-scanner convergence.
- Explainable Attention List; `HIGH ATTENTION` is review priority, not a buy signal.
- Manual AI Candidate Review v3.
- Alpaca read-only tracking for HIGH ATTENTION cases.
- Automatic 15m / 30m / 60m / 4h market-path outcomes.
- Detection-time Calibration Statistics.
- Manual screenshot-enhanced Trade Plan v3.
- Entry / Stop / T1 / T2 persistence and objective follow-up.
- Trade Plan Statistics grouped by detection conditions, plan characteristics and recognized strategy.
- Known Strategy Library recognition before automatic OpenAI Trade Plan requests.
- Full current regular-session 1-minute bars for opening-range/session-VWAP recognition.
- Automatic neutral Alpaca chart rendering.
- Strategy-aware automatic Trade Plan v3.
- Calibration Validator that can support, remain immature/neutral, or veto a setup.
- Persistent strategy/plan/alert deduplication.
- Deterministic final alert blockers.
- Windows/dashboard Entry / Stop / T1 / T2 trade alert path.

## Trade Plan v3

Trade Plan v3 still enforces its market-data correctness guardrails:

- checks actual Alpaca trade/quote/bar event timestamps separately from poll time;
- does not treat a freshly downloaded snapshot as proof that its latest trade is current;
- treats `IEX` as partial-venue data, not consolidated SIP/NBBO;
- treats legacy `zero_borrow=false` as UNKNOWN unless an explicit 0-borrow observation exists;
- ignores third-party BUY / SELL / ENTRY / SL / TP recommendations inside manual screenshots;
- does not directly compare differently defined TrendVision RV/volume with Alpaca IEX volume;
- blocks actionable plans when current market context is stale/unusable;
- blocks a fresh observed spread of 15%+ from actionable levels;
- keeps deterministic Entry / Stop / T1 / T2 coherence checks.

In automatic Strategy Library mode, the AI is additionally instructed **not to invent or switch to another setup**. It must evaluate the deterministic primary strategy and anchor its plan to that setup's structural key levels. A second deterministic anti-chase guard can downgrade a proposed plan if its entry is too far above the strategy reference.

## Automatic chart / bar context

For strategy recognition, TrendVisionAI requests the current day's regular-session Alpaca **1-minute bars from 09:30 New York time through now**. This allows the app to inspect opening ranges and calculate same-feed cumulative session VWAP.

For AI chart vision, TrendVisionAI renders a bounded recent portion of those bars as a neutral candlestick/volume image under:

```text
data\auto_trade_charts\...
```

The generated chart contains no BUY / SELL / SL / TP recommendations and inherits the configured Alpaca feed's limitations. With the default free `IEX` feed it remains partial-venue evidence.

Exact current planning price/quote still comes from fresh Trade Plan v3 market context rather than from chart pixels.

## Calibration and Trade Plan Statistics

TrendVisionAI continues collecting outcomes because the goal is to learn **how the known strategies behave specifically on the volatile stocks and scanner conditions TrendVision surfaces**.

Trade Plan Statistics can group actionable plan outcomes by items including:

- `STRATEGY: ORB_5M`
- `STRATEGY: BREAKOUT_RETEST`
- strategy family;
- strategy recognition score bucket;
- scanner signal / channel combination;
- RV / extension buckets;
- chart/risk/confidence characteristics.

For each group it tracks entry-observed count, resolved post-entry count, T1/T2 rates, stop-first rate and median post-entry MFE/MAE.

Sample maturity remains:

- `<5` — `TOO EARLY`
- `5-14` — `EARLY`
- `15-29` — `BUILDING`
- `30+` — `MORE STABLE`

These are evidence-maturity labels, not guaranteed win rates, statistical proof or expected profit.

## Calibration Validator v2

The old Candidate Qualification concept is now presented as **Calibration Validator**.

Detection-condition history can report:

- `INSUFFICIENT EVIDENCE`
- `MONITOR`
- `MONITOR / RISK`
- `EXPERIMENTALLY SUPPORTED`

Strategy-specific history can report:

- `CALIBRATION IMMATURE`
- `MATURE POSITIVE`
- `MATURE NEUTRAL`
- `MATURE NEGATIVE`

Current transparent maturity/classification thresholds are still intentionally conservative:

- 15 resolved cases before a pattern/strategy-specific row becomes mature enough to vote;
- positive: T1 reached at least 60% and stop-first no more than 30%;
- negative: T1 reached 30% or less, or stop-first at least 50%.

These thresholds are initial calibration logic, not claims that they are statistically optimal.

**Important change:** fewer than 30 global resolved cases no longer blocks a recognized known setup by itself. Immature history simply means the validator does not yet have a mature vote. Mature negative evidence can veto the final alert.

## Final alert gate

A final Trade Alert requires all of the following:

- regular US equity session (configured 09:30-16:00 America/New_York);
- recognized Strategy Library setup with `CANDIDATE` status;
- Trade Plan decision = `POTENTIAL TRADE`;
- fresh usable market context;
- fresh quote;
- known observed spread below the configured 15% guardrail;
- coherent Entry / Stop / T1 / T2;
- proposed entry not beyond the strategy's deterministic anti-chase limit;
- no `EXTREME` plan risk;
- chart structure not `DANGEROUS` / `UNCLEAR`;
- no multiple recent halt observations;
- no mature negative strategy-specific calibration;
- no mature negative detection-condition calibration veto;
- persistent duplicate protection.

A final notification is explicitly for **manual user decision/execution only**.

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
- Calibration Validator
- Strategy Library
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
- [x] Objective outcome calibration
- [x] Trade Plan v3 + objective Entry / Stop / T1 / T2 follow-up
- [x] Trade Plan Statistics
- [x] Automatic live chart / Trade Plan pipeline
- [x] Deterministic final alert blockers + Windows/dashboard alert path
- [x] Known Strategy Library v1
- [x] ORB / breakout-retest / HOD / first-pullback / session-VWAP setup recognition
- [x] Strategy-aware automatic Trade Plan instructions and anti-chase guardrails
- [x] Calibration changed from strategy inventor to validator/filter
- [x] Strategy-specific Trade Plan statistics tags
- [x] Strategy Library and Calibration Validator UI
- [ ] Run/observe the new strategy pipeline during regular market hours and fix real-world edge cases
- [ ] Accumulate strategy-specific cases
- [ ] Tune/freeze strategy thresholds and calibration veto rules from real evidence
- [ ] Validate frozen rules on new out-of-sample cases
- [ ] User manually decides and executes trades

## Safety / interpretation

TrendVisionAI is an experimental decision-support and calibration application. A recognized strategy, AI review, historical statistic, qualification label or trade-plan level does not guarantee profitability. No brokerage order is placed by the application.
