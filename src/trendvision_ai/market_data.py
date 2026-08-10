from __future__ import annotations

import json
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


KEYRING_SERVICE = "TrendVisionAI"
ALPACA_KEY_ACCOUNT = "alpaca_api_key_id"
ALPACA_SECRET_ACCOUNT = "alpaca_api_secret_key"
ALPACA_DATA_URL = "https://data.alpaca.markets"
DEFAULT_FEED = "iex"
DEFAULT_TRACKING_MINUTES = 240
TRACKING_HORIZONS = (15, 30, 60, 240)


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


def get_alpaca_credentials() -> tuple[str, str] | None:
    try:
        import keyring

        key_id = (keyring.get_password(KEYRING_SERVICE, ALPACA_KEY_ACCOUNT) or "").strip()
        secret = (keyring.get_password(KEYRING_SERVICE, ALPACA_SECRET_ACCOUNT) or "").strip()
        if key_id and secret:
            return key_id, secret
    except Exception:
        return None
    return None


def save_alpaca_credentials(key_id: str, secret: str) -> None:
    key_id = key_id.strip()
    secret = secret.strip()
    if not key_id or not secret:
        raise ValueError("Both Alpaca API key ID and secret are required.")
    import keyring

    keyring.set_password(KEYRING_SERVICE, ALPACA_KEY_ACCOUNT, key_id)
    keyring.set_password(KEYRING_SERVICE, ALPACA_SECRET_ACCOUNT, secret)


def delete_alpaca_credentials() -> None:
    try:
        import keyring

        for account in (ALPACA_KEY_ACCOUNT, ALPACA_SECRET_ACCOUNT):
            try:
                keyring.delete_password(KEYRING_SERVICE, account)
            except Exception:
                pass
    except Exception:
        pass


class AlpacaMarketDataError(RuntimeError):
    pass


class AlpacaMarketClient:
    """Small dependency-free client for Alpaca's multi-symbol stock snapshot API."""

    def __init__(self, key_id: str, secret: str, *, feed: str = DEFAULT_FEED) -> None:
        self.key_id = key_id.strip()
        self.secret = secret.strip()
        self.feed = (feed or DEFAULT_FEED).strip().lower()

    def fetch_snapshots(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        normalized = list(
            dict.fromkeys(symbol.upper().strip() for symbol in symbols if symbol.strip())
        )
        if not normalized:
            return {}

        query = urllib.parse.urlencode(
            {
                "symbols": ",".join(normalized),
                "feed": self.feed,
            }
        )
        request = urllib.request.Request(
            f"{ALPACA_DATA_URL}/v2/stocks/snapshots?{query}",
            headers={
                "APCA-API-KEY-ID": self.key_id,
                "APCA-API-SECRET-KEY": self.secret,
                "Accept": "application/json",
                "User-Agent": "TrendVisionAI/market-tracker",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="replace")
            except Exception:
                detail = str(exc)
            raise AlpacaMarketDataError(f"Alpaca HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise AlpacaMarketDataError(f"Alpaca connection error: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise AlpacaMarketDataError("Alpaca returned invalid JSON.") from exc

        if not isinstance(payload, dict):
            raise AlpacaMarketDataError("Unexpected Alpaca snapshot response.")
        return {
            str(symbol).upper(): value
            for symbol, value in payload.items()
            if isinstance(value, dict)
        }


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


def parse_snapshot(symbol: str, payload: dict[str, Any], *, feed: str) -> dict[str, Any]:
    trade = payload.get("latestTrade") or payload.get("latest_trade") or {}
    quote = payload.get("latestQuote") or payload.get("latest_quote") or {}
    bar = payload.get("minuteBar") or payload.get("minute_bar") or {}
    daily = payload.get("dailyBar") or payload.get("daily_bar") or {}

    bid = _num(quote.get("bp"))
    ask = _num(quote.get("ap"))
    spread = (ask - bid) if ask is not None and bid is not None and ask >= bid else None
    midpoint = (
        ((ask + bid) / 2.0)
        if ask is not None and bid is not None and ask > 0 and bid > 0
        else None
    )
    spread_pct = (spread / midpoint * 100.0) if spread is not None and midpoint else None

    return {
        "ticker": symbol.upper(),
        "captured_at": _now_iso(),
        "feed": feed,
        "trade_price": _num(trade.get("p")),
        "trade_size": _num(trade.get("s")),
        "trade_timestamp": trade.get("t"),
        "bid": bid,
        "ask": ask,
        "bid_size": _num(quote.get("bs")),
        "ask_size": _num(quote.get("as")),
        "quote_timestamp": quote.get("t"),
        "spread": spread,
        "spread_pct": spread_pct,
        "minute_timestamp": bar.get("t"),
        "minute_open": _num(bar.get("o")),
        "minute_high": _num(bar.get("h")),
        "minute_low": _num(bar.get("l")),
        "minute_close": _num(bar.get("c")),
        "minute_volume": _num(bar.get("v")),
        "minute_vwap": _num(bar.get("vw")),
        "day_volume": _num(daily.get("v")),
        "raw_json": json.dumps(payload, ensure_ascii=False, sort_keys=True),
    }


def _window_metrics(
    rows: list[dict[str, Any]],
    *,
    reference_time: datetime,
    target_minutes: int,
) -> dict[str, Any]:
    """Measure a price path after a reference time using stored Alpaca samples.

    The reference price is the first sample at/after reference_time. The first
    minute bar's high/low is intentionally ignored because that bar can contain
    trades from before the reference instant. Later minute bars can contribute
    their high/low to MFE/MAE.
    """
    target_minutes = max(1, int(target_minutes))
    deadline = reference_time + timedelta(minutes=target_minutes)
    now = _now()
    horizon_complete = _seconds_between(now, deadline) >= 0

    eligible: list[tuple[datetime, dict[str, Any]]] = []
    for row in rows:
        captured = _parse_time(row.get("captured_at"))
        if captured is None:
            continue
        delta = _seconds_between(captured, reference_time)
        if delta < 0 or delta > target_minutes * 60:
            continue
        eligible.append((captured, row))
    eligible.sort(key=lambda item: item[0])

    base = {
        "available": False,
        "target_minutes": target_minutes,
        "horizon_complete": horizon_complete,
        "fresh_to_horizon": False,
        "horizon_gap_seconds": None,
        "sample_count": 0,
        "coverage_minutes": 0.0,
        "coverage_pct": 0.0,
        "reference_price": None,
        "reference_captured_at": None,
        "last_price": None,
        "last_captured_at": None,
        "return_pct": None,
        "mfe_pct": None,
        "mae_pct": None,
        "peak_price": None,
        "peak_captured_at": None,
        "trough_price": None,
        "trough_captured_at": None,
        "time_to_peak_minutes": None,
        "time_to_trough_minutes": None,
        "max_minute_volume": None,
        "latest_spread_pct": None,
        "max_spread_pct": None,
        "feeds": [],
    }
    if not eligible:
        return base

    reference_captured, first = eligible[0]
    reference_price = _sample_price(first)
    if reference_price is None or reference_price <= 0:
        return base

    reference_minute = first.get("minute_timestamp")
    peak_price = reference_price
    trough_price = reference_price
    peak_time = reference_captured
    trough_time = reference_captured
    minute_volumes: dict[str, float] = {}
    spread_values: list[float] = []
    feeds: list[str] = []

    for captured, row in eligible:
        price = _sample_price(row)
        if price is None or price <= 0:
            continue

        same_reference_minute = bool(
            reference_minute
            and row.get("minute_timestamp")
            and row.get("minute_timestamp") == reference_minute
        )
        if captured == reference_captured or same_reference_minute:
            high = price
            low = price
        else:
            high = _num(row.get("minute_high")) or price
            low = _num(row.get("minute_low")) or price

        if high > peak_price:
            peak_price = high
            peak_time = captured
        if low < trough_price:
            trough_price = low
            trough_time = captured

        volume = _num(row.get("minute_volume"))
        if volume is not None:
            minute_key = str(row.get("minute_timestamp") or row.get("captured_at"))
            minute_volumes[minute_key] = max(volume, minute_volumes.get(minute_key, 0.0))

        spread_pct = _num(row.get("spread_pct"))
        if spread_pct is not None and spread_pct >= 0:
            spread_values.append(spread_pct)

        feed = str(row.get("feed") or "").upper()
        if feed and feed not in feeds:
            feeds.append(feed)

    last_captured, last = eligible[-1]
    last_price = _sample_price(last)
    coverage_minutes = max(0.0, _seconds_between(last_captured, reference_time) / 60.0)
    horizon_gap_seconds = max(0.0, _seconds_between(deadline, last_captured))
    fresh_to_horizon = bool(horizon_complete and horizon_gap_seconds <= 60.0)

    base.update(
        {
            "available": True,
            "sample_count": len(eligible),
            "coverage_minutes": coverage_minutes,
            "coverage_pct": min(100.0, coverage_minutes / target_minutes * 100.0),
            "reference_price": reference_price,
            "reference_captured_at": reference_captured.isoformat(timespec="seconds"),
            "last_price": last_price,
            "last_captured_at": last_captured.isoformat(timespec="seconds"),
            "return_pct": _pct_change(last_price, reference_price),
            "mfe_pct": _pct_change(peak_price, reference_price),
            "mae_pct": _pct_change(trough_price, reference_price),
            "peak_price": peak_price,
            "peak_captured_at": peak_time.isoformat(timespec="seconds"),
            "trough_price": trough_price,
            "trough_captured_at": trough_time.isoformat(timespec="seconds"),
            "time_to_peak_minutes": max(
                0.0, _seconds_between(peak_time, reference_time) / 60.0
            ),
            "time_to_trough_minutes": max(
                0.0, _seconds_between(trough_time, reference_time) / 60.0
            ),
            "max_minute_volume": max(minute_volumes.values()) if minute_volumes else None,
            "latest_spread_pct": _num(last.get("spread_pct")),
            "max_spread_pct": max(spread_values) if spread_values else None,
            "horizon_gap_seconds": horizon_gap_seconds,
            "fresh_to_horizon": fresh_to_horizon,
            "feeds": feeds,
        }
    )
    return base


def _return_at_horizon(
    rows: list[dict[str, Any]],
    *,
    reference_time: datetime,
    reference_price: float,
    minutes: int,
) -> float | None:
    deadline = reference_time + timedelta(minutes=minutes)
    if _seconds_between(_now(), deadline) < 0:
        return None

    candidates: list[tuple[datetime, dict[str, Any]]] = []
    for row in rows:
        captured = _parse_time(row.get("captured_at"))
        if captured is None:
            continue
        delta = _seconds_between(captured, reference_time)
        if 0 <= delta <= minutes * 60:
            candidates.append((captured, row))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    captured, row = candidates[-1]

    # The tracker polls every 15 seconds. Requiring a sample within the final
    # minute prevents a stale observation from masquerading as a completed
    # horizon if tracking stopped or failed.
    if _seconds_between(deadline, captured) > 60:
        return None
    return _pct_change(_sample_price(row), reference_price)


class MarketDataStore:
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
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS market_tracking_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    ended_at TEXT,
                    trigger_tier TEXT NOT NULL,
                    trigger_score INTEGER NOT NULL,
                    feed TEXT NOT NULL,
                    reference_price REAL,
                    reference_captured_at TEXT,
                    status TEXT NOT NULL DEFAULT 'ACTIVE',
                    last_error TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_market_tracking_active
                    ON market_tracking_sessions(status, expires_at);
                CREATE INDEX IF NOT EXISTS idx_market_tracking_ticker
                    ON market_tracking_sessions(ticker, started_at DESC);

                CREATE TABLE IF NOT EXISTS market_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    ticker TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    feed TEXT NOT NULL,
                    trade_price REAL,
                    trade_size REAL,
                    trade_timestamp TEXT,
                    bid REAL,
                    ask REAL,
                    bid_size REAL,
                    ask_size REAL,
                    quote_timestamp TEXT,
                    spread REAL,
                    spread_pct REAL,
                    minute_timestamp TEXT,
                    minute_open REAL,
                    minute_high REAL,
                    minute_low REAL,
                    minute_close REAL,
                    minute_volume REAL,
                    minute_vwap REAL,
                    day_volume REAL,
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(session_id) REFERENCES market_tracking_sessions(id)
                );
                CREATE INDEX IF NOT EXISTS idx_market_samples_session_time
                    ON market_samples(session_id, captured_at);
                CREATE INDEX IF NOT EXISTS idx_market_samples_ticker_time
                    ON market_samples(ticker, captured_at);
                """
            )

    def expire_sessions(self) -> int:
        now = _now_iso()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE market_tracking_sessions
                SET status='COMPLETE', ended_at=?
                WHERE status='ACTIVE' AND expires_at <= ?
                """,
                (now, now),
            )
            return int(cursor.rowcount or 0)

    def ensure_session(
        self,
        *,
        ticker: str,
        trigger_tier: str,
        trigger_score: int,
        feed: str,
        tracking_minutes: int = DEFAULT_TRACKING_MINUTES,
    ) -> dict[str, Any]:
        ticker = ticker.upper().strip()
        self.expire_sessions()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM market_tracking_sessions
                WHERE ticker=? AND status='ACTIVE'
                ORDER BY started_at DESC, id DESC LIMIT 1
                """,
                (ticker,),
            ).fetchone()
            if row is not None:
                return dict(row)

            started = _now()
            expires = started + timedelta(minutes=max(15, int(tracking_minutes)))
            cursor = connection.execute(
                """
                INSERT INTO market_tracking_sessions (
                    ticker, started_at, expires_at, trigger_tier, trigger_score, feed
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    ticker,
                    started.isoformat(timespec="seconds"),
                    expires.isoformat(timespec="seconds"),
                    trigger_tier,
                    int(trigger_score),
                    feed,
                ),
            )
            row = connection.execute(
                "SELECT * FROM market_tracking_sessions WHERE id=?",
                (cursor.lastrowid,),
            ).fetchone()
            return dict(row)

    def active_sessions(self, limit: int = 30) -> list[dict[str, Any]]:
        self.expire_sessions()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM market_tracking_sessions
                WHERE status='ACTIVE'
                ORDER BY started_at ASC, id ASC
                LIMIT ?
                """,
                (max(1, limit),),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_sample(self, session_id: int, sample: dict[str, Any]) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO market_samples (
                    session_id, ticker, captured_at, feed, trade_price, trade_size,
                    trade_timestamp, bid, ask, bid_size, ask_size, quote_timestamp,
                    spread, spread_pct, minute_timestamp, minute_open, minute_high,
                    minute_low, minute_close, minute_volume, minute_vwap, day_volume,
                    raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(session_id),
                    sample["ticker"],
                    sample["captured_at"],
                    sample["feed"],
                    sample.get("trade_price"),
                    sample.get("trade_size"),
                    sample.get("trade_timestamp"),
                    sample.get("bid"),
                    sample.get("ask"),
                    sample.get("bid_size"),
                    sample.get("ask_size"),
                    sample.get("quote_timestamp"),
                    sample.get("spread"),
                    sample.get("spread_pct"),
                    sample.get("minute_timestamp"),
                    sample.get("minute_open"),
                    sample.get("minute_high"),
                    sample.get("minute_low"),
                    sample.get("minute_close"),
                    sample.get("minute_volume"),
                    sample.get("minute_vwap"),
                    sample.get("day_volume"),
                    sample.get("raw_json") or "{}",
                ),
            )
            row = connection.execute(
                "SELECT reference_price FROM market_tracking_sessions WHERE id=?",
                (int(session_id),),
            ).fetchone()
            if row is not None and row[0] is None:
                reference = sample.get("trade_price") or sample.get("minute_close")
                if reference is not None:
                    connection.execute(
                        """
                        UPDATE market_tracking_sessions
                        SET reference_price=?, reference_captured_at=?, last_error=''
                        WHERE id=?
                        """,
                        (float(reference), sample["captured_at"], int(session_id)),
                    )
                else:
                    connection.execute(
                        "UPDATE market_tracking_sessions SET last_error='' WHERE id=?",
                        (int(session_id),),
                    )
            else:
                connection.execute(
                    "UPDATE market_tracking_sessions SET last_error='' WHERE id=?",
                    (int(session_id),),
                )

    def set_error(self, session_ids: list[int], message: str) -> None:
        if not session_ids:
            return
        placeholders = ",".join("?" for _ in session_ids)
        with self._connect() as connection:
            connection.execute(
                f"UPDATE market_tracking_sessions SET last_error=? WHERE id IN ({placeholders})",
                [message[:500], *[int(value) for value in session_ids]],
            )

    def list_sessions(self, limit: int = 100) -> list[dict[str, Any]]:
        self.expire_sessions()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM market_tracking_sessions
                ORDER BY started_at DESC, id DESC
                LIMIT ?
                """,
                (max(1, limit),),
            ).fetchall()
        return [dict(row) for row in rows]

    def _session_rows(self, session_id: int) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM market_samples
                WHERE session_id=?
                ORDER BY captured_at ASC, id ASC
                """,
                (int(session_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def session_horizon_metrics(
        self,
        *,
        session_id: int,
        horizon_minutes: int,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            session = connection.execute(
                "SELECT * FROM market_tracking_sessions WHERE id=?",
                (int(session_id),),
            ).fetchone()
        if session is None:
            return {
                "available": False,
                "target_minutes": max(1, int(horizon_minutes)),
                "horizon_complete": False,
                "error": "Unknown market tracking session.",
            }

        session_dict = dict(session)
        rows = self._session_rows(session_id)
        reference_time = _parse_time(session_dict.get("reference_captured_at"))
        if reference_time is None and rows:
            reference_time = _parse_time(rows[0].get("captured_at"))
        if reference_time is None:
            started = _parse_time(session_dict.get("started_at"))
            if started is None:
                return {
                    "available": False,
                    "target_minutes": max(1, int(horizon_minutes)),
                    "horizon_complete": False,
                    "error": "No valid session reference timestamp.",
                }
            # No successful market sample exists yet. Still return whether the
            # requested horizon has elapsed so the automatic outcome engine can
            # record insufficient coverage rather than waiting forever.
            result = _window_metrics(
                rows,
                reference_time=started,
                target_minutes=horizon_minutes,
            )
        else:
            result = _window_metrics(
                rows,
                reference_time=reference_time,
                target_minutes=horizon_minutes,
            )
        result["session_id"] = int(session_id)
        result["ticker"] = str(session_dict.get("ticker") or "").upper()
        result["session_started_at"] = session_dict.get("started_at")
        return result

    def session_metrics(self, session_id: int) -> dict[str, Any]:
        with self._connect() as connection:
            session = connection.execute(
                "SELECT * FROM market_tracking_sessions WHERE id=?",
                (int(session_id),),
            ).fetchone()
        if session is None:
            return {}

        result = dict(session)
        rows = self._session_rows(session_id)
        reference_time = _parse_time(result.get("reference_captured_at"))
        reference_price = _num(result.get("reference_price"))
        if reference_time is None and rows:
            reference_time = _parse_time(rows[0].get("captured_at"))
        if reference_price is None and rows:
            reference_price = _sample_price(rows[0])

        result["sample_count"] = len(rows)
        result["last_sample"] = rows[-1] if rows else None
        result["last_price"] = _sample_price(rows[-1]) if rows else None
        result["return_pct"] = _pct_change(result["last_price"], reference_price)
        result["mfe_pct"] = None
        result["mae_pct"] = None
        result["peak_price"] = None
        result["trough_price"] = None
        result["time_to_peak_minutes"] = None
        result["time_to_trough_minutes"] = None
        result["max_minute_volume"] = None
        result["max_spread_pct"] = None
        result["elapsed_minutes"] = 0.0
        result["horizon_returns"] = {minutes: None for minutes in TRACKING_HORIZONS}

        if reference_time is not None and rows:
            metrics = _window_metrics(
                rows,
                reference_time=reference_time,
                target_minutes=DEFAULT_TRACKING_MINUTES,
            )
            for key in (
                "mfe_pct",
                "mae_pct",
                "peak_price",
                "trough_price",
                "time_to_peak_minutes",
                "time_to_trough_minutes",
                "max_minute_volume",
                "max_spread_pct",
                "coverage_minutes",
            ):
                if key in metrics:
                    result[key] = metrics[key]
            result["elapsed_minutes"] = float(metrics.get("coverage_minutes") or 0.0)
            if reference_price is not None and reference_price > 0:
                result["horizon_returns"] = {
                    minutes: _return_at_horizon(
                        rows,
                        reference_time=reference_time,
                        reference_price=reference_price,
                        minutes=minutes,
                    )
                    for minutes in TRACKING_HORIZONS
                }
        return result

    def review_metrics(
        self,
        *,
        ticker: str,
        review_created_at: str,
        horizon_minutes: int = 30,
    ) -> dict[str, Any]:
        """Return objective Alpaca measurements tied to one AI review timestamp."""
        review_time = _parse_time(review_created_at)
        if review_time is None:
            return {
                "available": False,
                "target_minutes": max(1, int(horizon_minutes)),
                "horizon_complete": False,
                "error": "Invalid review timestamp.",
            }

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT s.*
                FROM market_samples s
                WHERE UPPER(s.ticker)=?
                ORDER BY s.captured_at ASC, s.id ASC
                """,
                (ticker.upper().strip(),),
            ).fetchall()

        result = _window_metrics(
            [dict(row) for row in rows],
            reference_time=review_time,
            target_minutes=horizon_minutes,
        )
        result["ticker"] = ticker.upper().strip()
        result["review_created_at"] = review_created_at
        return result
