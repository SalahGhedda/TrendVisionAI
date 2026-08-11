from __future__ import annotations

from datetime import datetime, timedelta, timezone

from trendvision_ai.strategy_library import detect_known_setups, strategy_catalog


def _bar(timestamp: datetime | None, o: float, h: float, l: float, c: float, v: float = 1000.0):
    return {
        "timestamp": timestamp.isoformat().replace("+00:00", "Z") if timestamp else None,
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": v,
        "vwap": (h + l + c) / 3.0,
    }


def test_catalog_contains_expected_known_setup_families():
    ids = {row["strategy_id"] for row in strategy_catalog()}
    assert "ORB_5M" in ids
    assert "ORB_15M" in ids
    assert "BREAKOUT_RETEST" in ids
    assert "HOD_BREAKOUT" in ids
    assert "FIRST_PULLBACK" in ids
    assert "VWAP_RECLAIM_HOLD" in ids


def test_detects_five_minute_opening_range_breakout():
    start = datetime(2026, 8, 10, 13, 30, tzinfo=timezone.utc)  # 09:30 New York
    values = [
        (9.70, 9.90, 9.60, 9.82, 1000),
        (9.82, 10.00, 9.78, 9.92, 1100),
        (9.92, 9.98, 9.84, 9.88, 900),
        (9.88, 9.97, 9.80, 9.93, 1000),
        (9.93, 9.99, 9.86, 9.94, 950),
        (9.94, 9.99, 9.88, 9.95, 1050),
        (9.95, 10.00, 9.90, 9.96, 980),
        (9.96, 10.00, 9.91, 9.95, 1020),
        (9.95, 10.20, 9.94, 10.15, 3200),
        (10.15, 10.28, 10.10, 10.22, 2200),
        (10.22, 10.33, 10.18, 10.28, 1800),
        (10.28, 10.36, 10.24, 10.31, 1700),
    ]
    bars = [
        _bar(start + timedelta(minutes=index), *value)
        for index, value in enumerate(values)
    ]
    context = detect_known_setups(bars)
    ids = {match["strategy_id"] for match in context["matches"]}
    assert context["recognized"] is True
    assert "ORB_5M" in ids


def test_detects_breakout_retest():
    bars = []
    for index in range(22):
        close = 9.80 + (index % 4) * 0.03
        bars.append(_bar(None, close - 0.02, min(10.0, close + 0.08), close - 0.08, close, 1000 + index * 5))
    bars.extend(
        [
            _bar(None, 9.95, 10.22, 9.94, 10.15, 3000),
            _bar(None, 10.14, 10.16, 9.97, 10.05, 1500),
            _bar(None, 10.05, 10.20, 10.03, 10.16, 1600),
            _bar(None, 10.16, 10.28, 10.12, 10.23, 1700),
            _bar(None, 10.23, 10.31, 10.18, 10.27, 1600),
            _bar(None, 10.27, 10.34, 10.22, 10.30, 1500),
            _bar(None, 10.30, 10.35, 10.24, 10.29, 1400),
            _bar(None, 10.29, 10.36, 10.25, 10.32, 1450),
        ]
    )
    context = detect_known_setups(bars)
    ids = {match["strategy_id"] for match in context["matches"]}
    assert "BREAKOUT_RETEST" in ids


def test_flat_chart_has_no_known_setup():
    start = datetime(2026, 8, 10, 14, 0, tzinfo=timezone.utc)
    bars = [
        _bar(start + timedelta(minutes=index), 10.0, 10.03, 9.97, 10.0, 1000)
        for index in range(30)
    ]
    context = detect_known_setups(bars)
    assert context["recognized"] is False
    assert context["primary"] is None
