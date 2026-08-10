from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .market_data import MarketDataStore


TRADE_PLAN_VERSION = 1
PLAN_HORIZON_MINUTES = 240
SUPPORTED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def _now_iso() -> str:
    return _now().isoformat(timespec="seconds")


def _parse_time(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _seconds_between(later: datetime, earlier: datetime) -> float:
    try:
        return (later - earlier).total_seconds()
    except TypeError:
        return (later.replace(tzinfo=None) - earlier.replace(tzinfo=None)).total_seconds()


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sample_price(row: dict[str, Any] | sqlite3.Row) -> float | None:
    return _num(row["trade_price"]) or _num(row["minute_close"])


def _pct_change(value: float | None, reference: float | None) -> float | None:
    if value is None or reference is None or reference <= 0:
        return None
    return ((value / reference) - 1.0) * 100.0


@dataclass(slots=True)
class TradePlanResult:
    ticker: str
    model: str
    decision: str
    confidence: str
    risk_level: str
    chart_structure: str
    setup_type: str
    summary: str
    entry_low: float | None
    entry_high: float | None
    stop_loss: float | None
    target_1: float | None
    target_2: float | None
    risk_reward_target_1: float | None
    risk_reward_target_2: float | None
    entry_trigger: str
    invalidation: str
    positive_factors: list[str]
    risk_factors: list[str]
    chart_observations: list[str]
    what_to_confirm: list[str]
    created_at: str
    plan_version: int = TRADE_PLAN_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_TRADE_PLAN_SCHEMA = {
    "type": "json_schema",
    "name": "trendvision_trade_plan_v1",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "decision": {"type": "string", "enum": ["REJECT", "WATCH", "POTENTIAL TRADE"]},
            "confidence": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"]},
            "risk_level": {"type": "string", "enum": ["LOW", "MODERATE", "HIGH", "EXTREME"]},
            "chart_structure": {"type": "string", "enum": ["STRONG", "MODERATE", "WEAK", "DANGEROUS", "UNCLEAR"]},
            "setup_type": {"type": "string"},
            "summary": {"type": "string"},
            "entry_low": {"type": ["number", "null"]},
            "entry_high": {"type": ["number", "null"]},
            "stop_loss": {"type": ["number", "null"]},
            "target_1": {"type": ["number", "null"]},
            "target_2": {"type": ["number", "null"]},
            "entry_trigger": {"type": "string"},
            "invalidation": {"type": "string"},
            "positive_factors": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
            "risk_factors": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
            "chart_observations": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
            "what_to_confirm": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
        },
        "required": [
            "decision", "confidence", "risk_level", "chart_structure", "setup_type", "summary",
            "entry_low", "entry_high", "stop_loss", "target_1", "target_2", "entry_trigger",
            "invalidation", "positive_factors", "risk_factors", "chart_observations", "what_to_confirm",
        ],
    },
}


_TRADE_PLAN_INSTRUCTIONS = """You are the experimental trade-plan review layer of TrendVisionAI.
You receive structured TrendVision Discord scanner evidence, exact Alpaca observations captured by the app, and one user-supplied current chart screenshot.

The human user makes every trading decision manually. Your output is an experimental plan that will be saved and objectively evaluated later; it is not an order and you must not promise profit.

Rules:
- Use TrendVision structured data for scanner convergence, signals, RV, halts, borrow, whale direction, news and other captured facts.
- Use Alpaca values for exact numerical market prices, bid/ask, spread, volume and timestamps.
- Use the screenshot for visual structure: trend shape, breakout/pullback behavior, visible support/resistance, wicks, consolidation, extension and visible indicators.
- If screenshot text conflicts with Alpaca numbers, prefer Alpaca for exact numbers and mention a material conflict.
- Do not invent hidden indicators, Level 2, options flow, short data, news, SEC facts, borrow data or volume values that are not supplied.
- Very extended moves, multiple halts, large spreads, violent wicks, failed breakouts or reversal structure materially increase risk.
- POTENTIAL TRADE requires a coherent LONG-side plan: entry zone, stop below entry, and targets above entry.
- If chart structure is unclear, exact market context is missing, or risk is too high, choose WATCH or REJECT and return null price levels.
- Do not choose POTENTIAL TRADE merely because the attention score is high.
- Entry must be tied to support, breakout, pullback or confirmation logic rather than a generic percentage.
- Stop must represent setup invalidation rather than a generic percentage.
- Targets should be tied to visible structure/resistance or a defensible reward/risk objective.
Return only the requested structured result."""


def _image_data_url(image_path: str | Path) -> tuple[str, str]:
    path = Path(image_path)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Chart screenshot not found: {path}")
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_IMAGE_SUFFIXES:
        raise ValueError("Chart screenshot must be PNG, JPG/JPEG, WEBP, or GIF.")
    data = path.read_bytes()
    if len(data) > 15 * 1024 * 1024:
        raise ValueError("Chart screenshot is too large; keep it under 15 MB.")
    mime = mimetypes.types_map.get(suffix) or "image/png"
    if suffix in {".jpg", ".jpeg"}:
        mime = "image/jpeg"
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{encoded}", hashlib.sha256(data).hexdigest()


def latest_market_context(database_path: str | Path, ticker: str) -> dict[str, Any]:
    market = MarketDataStore(database_path)
    ticker = ticker.upper().strip()
    session = next(
        (row for row in market.list_sessions(limit=500) if str(row.get("ticker") or "").upper() == ticker),
        None,
    )
    if session is None:
        return {"available": False, "reason": "No Alpaca tracking session exists for this ticker."}

    metrics = market.session_metrics(int(session["id"]))
    latest = metrics.get("last_sample") or {}
    return {
        "available": bool(latest),
        "session_id": metrics.get("id"),
        "session_status": metrics.get("status"),
        "session_started_at": metrics.get("started_at"),
        "trigger_tier": metrics.get("trigger_tier"),
        "trigger_score": metrics.get("trigger_score"),
        "feed": metrics.get("feed"),
        "reference_price": metrics.get("reference_price"),
        "reference_captured_at": metrics.get("reference_captured_at"),
        "current_return_pct": metrics.get("return_pct"),
        "mfe_pct": metrics.get("mfe_pct"),
        "mae_pct": metrics.get("mae_pct"),
        "elapsed_minutes": metrics.get("elapsed_minutes"),
        "latest": {
            "captured_at": latest.get("captured_at"),
            "trade_price": latest.get("trade_price"),
            "trade_size": latest.get("trade_size"),
            "bid": latest.get("bid"),
            "ask": latest.get("ask"),
            "bid_size": latest.get("bid_size"),
            "ask_size": latest.get("ask_size"),
            "spread": latest.get("spread"),
            "spread_pct": latest.get("spread_pct"),
            "minute_timestamp": latest.get("minute_timestamp"),
            "minute_open": latest.get("minute_open"),
            "minute_high": latest.get("minute_high"),
            "minute_low": latest.get("minute_low"),
            "minute_close": latest.get("minute_close"),
            "minute_volume": latest.get("minute_volume"),
            "minute_vwap": latest.get("minute_vwap"),
            "day_volume": latest.get("day_volume"),
        },
    }


def build_trade_plan_snapshot(
    *,
    database_path: str | Path,
    trendvision_snapshot: dict[str, Any],
    latest_ai_review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ticker = str(trendvision_snapshot.get("ticker") or "").upper().strip()
    return {
        "ticker": ticker,
        "trade_plan_version": TRADE_PLAN_VERSION,
        "generated_at": _now_iso(),
        "purpose": "Experimental manual trade-plan evaluation. No brokerage order is placed.",
        "important_limitations": [
            "The human user decides whether to trade.",
            "IEX observations are not consolidated SIP when IEX is selected.",
            "Discord Windows notifications can omit lower rich-embed fields.",
            "Chart vision is qualitative; exact numeric calculations should use supplied Alpaca fields.",
            "The plan is measured afterward so the system can learn whether its proposed levels were useful.",
        ],
        "trendvision": trendvision_snapshot,
        "latest_ai_candidate_review": latest_ai_review,
        "alpaca_market_context": latest_market_context(database_path, ticker),
    }


def _risk_reward(entry_high: float | None, stop: float | None, target: float | None) -> float | None:
    if entry_high is None or stop is None or target is None or entry_high <= stop:
        return None
    risk = entry_high - stop
    reward = target - entry_high
    return reward / risk if risk > 0 and reward > 0 else None


def calibrate_trade_plan_payload(parsed: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    result = dict(parsed)
    decision = str(result.get("decision") or "WATCH").upper()
    risk = str(result.get("risk_level") or "HIGH").upper()
    structure = str(result.get("chart_structure") or "UNCLEAR").upper()
    keys = ("entry_low", "entry_high", "stop_loss", "target_1", "target_2")
    numbers = {key: _num(result.get(key)) for key in keys}

    market = snapshot.get("alpaca_market_context") or {}
    latest = market.get("latest") or {}
    current_price = _num(latest.get("trade_price")) or _num(latest.get("minute_close"))
    events = (((snapshot.get("trendvision") or {}).get("recent_convergence") or {}).get("events") or [])
    halt_count = sum(
        1 for event in events
        if str(event.get("channel") or "") == "halt-scanner" or (event.get("data") or {}).get("halt_status")
    )
    changes = [
        abs(value) for value in (_num((event.get("data") or {}).get("change_pct")) for event in events)
        if value is not None
    ]
    max_change = max(changes, default=0.0)

    coherent = (
        all(numbers[key] is not None and numbers[key] > 0 for key in keys)
        and numbers["entry_low"] <= numbers["entry_high"]
        and numbers["stop_loss"] < numbers["entry_low"]
        and numbers["target_1"] > numbers["entry_high"]
        and numbers["target_2"] >= numbers["target_1"]
    )
    warnings = [str(value) for value in result.get("risk_factors") or []]

    if decision == "POTENTIAL TRADE":
        if current_price is None:
            warnings.append("No usable current Alpaca price was available at review time.")
            decision = "WATCH"
        elif not coherent:
            warnings.append("Model price levels failed deterministic long-plan coherence checks.")
            decision = "WATCH"
        elif risk == "EXTREME":
            warnings.append("Extreme-risk cases are not promoted to POTENTIAL TRADE by v1.")
            decision = "WATCH"
        elif structure in {"DANGEROUS", "UNCLEAR"}:
            warnings.append("Dangerous/unclear chart structure is not promoted to POTENTIAL TRADE by v1.")
            decision = "WATCH"
        elif halt_count >= 2 or max_change >= 100:
            warnings.append("Multiple halts or a 100%+ recent move requires further confirmation.")
            decision = "WATCH"

    if decision != "POTENTIAL TRADE":
        numbers = {key: None for key in keys}

    result.update(numbers)
    result["decision"] = decision
    result["risk_factors"] = list(dict.fromkeys(warnings))
    return result


def analyze_trade_plan(
    snapshot: dict[str, Any],
    *,
    image_path: str | Path,
    api_key: str,
    model: str,
) -> TradePlanResult:
    from openai import OpenAI

    image_url, image_sha = _image_data_url(image_path)
    request_snapshot = dict(snapshot)
    request_snapshot["chart_screenshot"] = {
        "sha256": image_sha,
        "role": "User-supplied current chart screenshot",
    }

    client = OpenAI(api_key=api_key)
    response = client.responses.create(
        model=model,
        instructions=_TRADE_PLAN_INSTRUCTIONS,
        input=[{
            "role": "user",
            "content": [
                {"type": "input_text", "text": json.dumps(request_snapshot, ensure_ascii=False, separators=(",", ":"))},
                {"type": "input_image", "image_url": image_url, "detail": "high"},
            ],
        }],
        text={"format": _TRADE_PLAN_SCHEMA},
    )
    parsed = calibrate_trade_plan_payload(json.loads(response.output_text), request_snapshot)
    entry_high = _num(parsed.get("entry_high"))
    stop = _num(parsed.get("stop_loss"))
    target_1 = _num(parsed.get("target_1"))
    target_2 = _num(parsed.get("target_2"))

    return TradePlanResult(
        ticker=str(request_snapshot.get("ticker") or "?").upper(),
        model=model,
        decision=str(parsed["decision"]),
        confidence=str(parsed["confidence"]),
        risk_level=str(parsed["risk_level"]),
        chart_structure=str(parsed["chart_structure"]),
        setup_type=str(parsed.get("setup_type") or ""),
        summary=str(parsed.get("summary") or ""),
        entry_low=_num(parsed.get("entry_low")),
        entry_high=entry_high,
        stop_loss=stop,
        target_1=target_1,
        target_2=target_2,
        risk_reward_target_1=_risk_reward(entry_high, stop, target_1),
        risk_reward_target_2=_risk_reward(entry_high, stop, target_2),
        entry_trigger=str(parsed.get("entry_trigger") or ""),
        invalidation=str(parsed.get("invalidation") or ""),
        positive_factors=[str(value) for value in parsed.get("positive_factors") or []],
        risk_factors=[str(value) for value in parsed.get("risk_factors") or []],
        chart_observations=[str(value) for value in parsed.get("chart_observations") or []],
        what_to_confirm=[str(value) for value in parsed.get("what_to_confirm") or []],
        created_at=_now_iso(),
    )


class TradePlanStore:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=3.0)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS trade_plans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    model TEXT NOT NULL,
                    plan_version INTEGER NOT NULL,
                    decision TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    risk_level TEXT NOT NULL,
                    chart_structure TEXT NOT NULL,
                    setup_type TEXT NOT NULL DEFAULT '',
                    entry_low REAL,
                    entry_high REAL,
                    stop_loss REAL,
                    target_1 REAL,
                    target_2 REAL,
                    screenshot_path TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_trade_plans_ticker_created
                    ON trade_plans(ticker, created_at DESC);
                CREATE TABLE IF NOT EXISTS trade_plan_evaluations (
                    plan_id INTEGER PRIMARY KEY,
                    evaluated_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    horizon_minutes INTEGER NOT NULL,
                    horizon_complete INTEGER NOT NULL DEFAULT 0,
                    entry_reached_at TEXT,
                    entry_price REAL,
                    target_1_hit_at TEXT,
                    target_2_hit_at TEXT,
                    stop_hit_at TEXT,
                    final_price REAL,
                    max_return_pct REAL,
                    max_drawdown_pct REAL,
                    sample_count INTEGER NOT NULL DEFAULT 0,
                    evaluation_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(plan_id) REFERENCES trade_plans(id)
                );
            """)

    def save(self, result: TradePlanResult, snapshot: dict[str, Any], screenshot_path: str | Path) -> int:
        with self._connect() as connection:
            cursor = connection.execute("""
                INSERT INTO trade_plans (
                    ticker, created_at, model, plan_version, decision, confidence, risk_level,
                    chart_structure, setup_type, entry_low, entry_high, stop_loss, target_1,
                    target_2, screenshot_path, result_json, snapshot_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                result.ticker, result.created_at, result.model, result.plan_version, result.decision,
                result.confidence, result.risk_level, result.chart_structure, result.setup_type,
                result.entry_low, result.entry_high, result.stop_loss, result.target_1, result.target_2,
                str(screenshot_path), json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True),
                json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
            ))
            return int(cursor.lastrowid)

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        for source, target in (("result_json", "result"), ("snapshot_json", "snapshot"), ("evaluation_json", "evaluation")):
            if source not in item:
                continue
            try:
                decoded = json.loads(item.get(source) or "{}")
            except json.JSONDecodeError:
                decoded = {}
            item[target] = decoded if isinstance(decoded, dict) else {}
        return item

    def latest(self, ticker: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("""
                SELECT p.*, e.status AS evaluation_status, e.horizon_complete,
                       e.entry_reached_at, e.entry_price, e.target_1_hit_at, e.target_2_hit_at,
                       e.stop_hit_at, e.final_price, e.max_return_pct, e.max_drawdown_pct,
                       e.sample_count AS evaluation_sample_count, e.evaluation_json
                FROM trade_plans p LEFT JOIN trade_plan_evaluations e ON e.plan_id=p.id
                WHERE UPPER(p.ticker)=?
                ORDER BY p.created_at DESC, p.id DESC LIMIT 1
            """, (ticker.upper().strip(),)).fetchone()
        return self._decode(row) if row is not None else None

    def list_plans(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("""
                SELECT p.*, e.status AS evaluation_status, e.horizon_complete,
                       e.entry_reached_at, e.entry_price, e.target_1_hit_at, e.target_2_hit_at,
                       e.stop_hit_at, e.final_price, e.max_return_pct, e.max_drawdown_pct,
                       e.sample_count AS evaluation_sample_count, e.evaluation_json
                FROM trade_plans p LEFT JOIN trade_plan_evaluations e ON e.plan_id=p.id
                ORDER BY p.created_at DESC, p.id DESC LIMIT ?
            """, (max(1, int(limit)),)).fetchall()
        return [self._decode(row) for row in rows]

    def _plan(self, plan_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM trade_plans WHERE id=?", (int(plan_id),)).fetchone()
        return self._decode(row) if row is not None else None

    def evaluate(self, plan_id: int, *, horizon_minutes: int = PLAN_HORIZON_MINUTES) -> dict[str, Any] | None:
        plan = self._plan(plan_id)
        if plan is None:
            return None
        created = _parse_time(plan.get("created_at"))
        if created is None:
            return None
        horizon_minutes = max(1, int(horizon_minutes))
        deadline = created + timedelta(minutes=horizon_minutes)
        complete = _seconds_between(_now(), deadline) >= 0

        entry_low = _num(plan.get("entry_low"))
        entry_high = _num(plan.get("entry_high"))
        stop = _num(plan.get("stop_loss"))
        target_1 = _num(plan.get("target_1"))
        target_2 = _num(plan.get("target_2"))

        with self._connect() as connection:
            rows = connection.execute("""
                SELECT * FROM market_samples WHERE UPPER(ticker)=?
                ORDER BY captured_at ASC, id ASC
            """, (str(plan.get("ticker") or "").upper(),)).fetchall()

        samples: list[tuple[datetime, float]] = []
        for row in rows:
            item = dict(row)
            captured = _parse_time(item.get("captured_at"))
            price = _sample_price(item)
            if captured is None or price is None or price <= 0:
                continue
            delta = _seconds_between(captured, created)
            if 0 <= delta <= horizon_minutes * 60:
                samples.append((captured, price))

        actionable = (
            str(plan.get("decision") or "") == "POTENTIAL TRADE"
            and all(value is not None for value in (entry_low, entry_high, stop, target_1, target_2))
        )
        if not actionable:
            return self._save_evaluation(plan_id, horizon_minutes, {
                "status": "NO ACTIONABLE LEVELS", "reason": "The saved review did not produce a POTENTIAL TRADE plan.",
                "horizon_complete": complete, "sample_count": len(samples),
            })
        if not samples:
            return self._save_evaluation(plan_id, horizon_minutes, {
                "status": "INSUFFICIENT DATA" if complete else "WAITING FOR MARKET DATA",
                "reason": "No usable Alpaca samples exist after the plan timestamp.",
                "horizon_complete": complete, "sample_count": 0,
            })

        entry_index = next((i for i, (_t, price) in enumerate(samples) if entry_low <= price <= entry_high), None)
        if entry_index is None:
            return self._save_evaluation(plan_id, horizon_minutes, {
                "status": "ENTRY NOT REACHED" if complete else "WAITING FOR ENTRY",
                "reason": "Sampled trade price has not entered the proposed entry zone.",
                "horizon_complete": complete, "sample_count": len(samples), "final_price": samples[-1][1],
            })

        entry_time, entry_price = samples[entry_index]
        t1_time = None
        t2_time = None
        stop_time = None
        prices: list[float] = []
        for captured, price in samples[entry_index:]:
            prices.append(price)
            if price <= stop:
                stop_time = captured
                break
            if price >= target_2:
                t1_time = t1_time or captured
                t2_time = captured
                break
            if price >= target_1 and t1_time is None:
                t1_time = captured

        if t2_time is not None:
            status = "TARGET 2 HIT"
        elif stop_time is not None and t1_time is not None:
            status = "TARGET 1 THEN STOP"
        elif stop_time is not None:
            status = "STOP HIT FIRST"
        elif t1_time is not None and complete:
            status = "TARGET 1 ONLY"
        elif t1_time is not None:
            status = "TARGET 1 HIT / OPEN"
        elif complete:
            status = "NO TARGET / NO STOP"
        else:
            status = "OPEN / IN PROGRESS"

        return self._save_evaluation(plan_id, horizon_minutes, {
            "status": status,
            "reason": "Objective sampled-price evaluation of the saved plan.",
            "horizon_complete": complete,
            "sample_count": len(samples),
            "entry_reached_at": entry_time.isoformat(timespec="seconds"),
            "entry_price": entry_price,
            "target_1_hit_at": t1_time.isoformat(timespec="seconds") if t1_time else None,
            "target_2_hit_at": t2_time.isoformat(timespec="seconds") if t2_time else None,
            "stop_hit_at": stop_time.isoformat(timespec="seconds") if stop_time else None,
            "final_price": prices[-1] if prices else entry_price,
            "max_return_pct": _pct_change(max(prices), entry_price) if prices else 0.0,
            "max_drawdown_pct": _pct_change(min(prices), entry_price) if prices else 0.0,
        })

    def _save_evaluation(self, plan_id: int, horizon_minutes: int, evaluation: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("""
                INSERT INTO trade_plan_evaluations (
                    plan_id, evaluated_at, status, horizon_minutes, horizon_complete,
                    entry_reached_at, entry_price, target_1_hit_at, target_2_hit_at,
                    stop_hit_at, final_price, max_return_pct, max_drawdown_pct, sample_count, evaluation_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(plan_id) DO UPDATE SET
                    evaluated_at=excluded.evaluated_at, status=excluded.status,
                    horizon_minutes=excluded.horizon_minutes, horizon_complete=excluded.horizon_complete,
                    entry_reached_at=excluded.entry_reached_at, entry_price=excluded.entry_price,
                    target_1_hit_at=excluded.target_1_hit_at, target_2_hit_at=excluded.target_2_hit_at,
                    stop_hit_at=excluded.stop_hit_at, final_price=excluded.final_price,
                    max_return_pct=excluded.max_return_pct, max_drawdown_pct=excluded.max_drawdown_pct,
                    sample_count=excluded.sample_count, evaluation_json=excluded.evaluation_json
            """, (
                int(plan_id), _now_iso(), str(evaluation.get("status") or "UNKNOWN"), int(horizon_minutes),
                1 if evaluation.get("horizon_complete") else 0, evaluation.get("entry_reached_at"),
                evaluation.get("entry_price"), evaluation.get("target_1_hit_at"), evaluation.get("target_2_hit_at"),
                evaluation.get("stop_hit_at"), evaluation.get("final_price"), evaluation.get("max_return_pct"),
                evaluation.get("max_drawdown_pct"), int(evaluation.get("sample_count") or 0),
                json.dumps(evaluation, ensure_ascii=False, sort_keys=True),
            ))
        return evaluation

    def refresh_evaluations(self, limit: int = 200) -> int:
        changed = 0
        for plan in self.list_plans(limit=limit):
            before = str(plan.get("evaluation_status") or "")
            after = str((self.evaluate(int(plan["id"])) or {}).get("status") or "")
            if before != after:
                changed += 1
        return changed
