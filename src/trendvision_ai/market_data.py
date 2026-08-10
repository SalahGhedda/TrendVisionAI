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


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


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
        normalized = list(dict.fromkeys(symbol.upper().strip() for symbol in symbols if symbol.strip()))
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


def parse_snapshot(symbol: str, payload: dict[str, Any], *, feed: str) -> dict[str, Any]:
    trade = payload.get("latestTrade") or payload.get("latest_trade") or {}
    quote = payload.get("latestQuote") or payload.get("latest_quote") or {}
    bar = payload.get("minuteBar") or payload.get("minute_bar") or {}
    daily = payload.get("dailyBar") or payload.get("daily_bar") or {}

    bid = _num(quote.get("bp"))
    ask = _num(quote.get("ap"))
    spread = (ask - bid) if ask is not None and bid is not None and ask >= bid else None
    midpoint = ((ask + bid) / 2.0) if ask is not None and bid is not None and ask > 0 and bid > 0 else None
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

            started = datetime.now(timezone.utc).astimezone()
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
                    int(session_id), sample["ticker"], sample["captured_at"], sample["feed"],
                    sample.get("trade_price"), sample.get("trade_size"), sample.get("trade_timestamp"),
                    sample.get("bid"), sample.get("ask"), sample.get("bid_size"), sample.get("ask_size"),
                    sample.get("quote_timestamp"), sample.get("spread"), sample.get("spread_pct"),
                    sample.get("minute_timestamp"), sample.get("minute_open"), sample.get("minute_high"),
                    sample.get("minute_low"), sample.get("minute_close"), sample.get("minute_volume"),
                    sample.get("minute_vwap"), sample.get("day_volume"), sample.get("raw_json") or "{}",
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

    def session_metrics(self, session_id: int) -> dict[str, Any]:
        with self._connect() as connection:
            session = connection.execute(
                "SELECT * FROM market_tracking_sessions WHERE id=?",
                (int(session_id),),
            ).fetchone()
            latest = connection.execute(
                """
                SELECT * FROM market_samples
                WHERE session_id=? ORDER BY captured_at DESC, id DESC LIMIT 1
                """,
                (int(session_id),),
            ).fetchone()
            aggregate = connection.execute(
                """
                SELECT COUNT(*) AS sample_count,
                       MAX(COALESCE(minute_high, trade_price, minute_close)) AS max_price,
                       MIN(COALESCE(minute_low, trade_price, minute_close)) AS min_price
                FROM market_samples WHERE session_id=?
                """,
                (int(session_id),),
            ).fetchone()
        if session is None:
            return {}
        result = dict(session)
        result["sample_count"] = int(aggregate["sample_count"] or 0) if aggregate else 0
        result["last_sample"] = dict(latest) if latest is not None else None

        reference = _num(session["reference_price"])
        last_price = None
        if latest is not None:
            last_price = _num(latest["trade_price"]) or _num(latest["minute_close"])
        result["last_price"] = last_price
        result["return_pct"] = ((last_price / reference) - 1.0) * 100.0 if reference and last_price else None

        max_price = _num(aggregate["max_price"]) if aggregate else None
        min_price = _num(aggregate["min_price"]) if aggregate else None
        result["mfe_pct"] = ((max_price / reference) - 1.0) * 100.0 if reference and max_price else None
        result["mae_pct"] = ((min_price / reference) - 1.0) * 100.0 if reference and min_price else None
        return result
