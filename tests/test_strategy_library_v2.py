from __future__ import annotations

from datetime import datetime, timedelta

from trendvision_ai.strategy_library_v2 import detect_known_setups, strategy_catalog


def _bar(timestamp: datetime, o: float, h: float, l: float, c: float, volume: float = 1000) -> dict:
    return {
        "timestamp": timestamp.isoformat(),
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": volume,
        "vwap": (h + l + c) / 3.0,
    }


def _premarket_breakout_bars(*, second_break: bool = False) -> list[dict]:
    bars: list[dict] = []
    start = datetime.fromisoformat("2026-08-11T08:00:00-04:00")
    price = 1.82
    for index in range(8):
        t = start + timedelta(minutes=index * 8)
        high = min(2.00, price + 0.05)
        bars.append(_bar(t, price, high, price - 0.04, high - 0.01, 2000 + index * 100))
        price += 0.02

    regular = datetime.fromisoformat("2026-08-11T09:30:00-04:00")
    bars.extend(
        [
            _bar(regular, 1.94, 1.99, 1.91, 1.96, 5000),
            _bar(regular + timedelta(minutes=1), 1.96, 2.00, 1.94, 1.98, 5200),
            _bar(regular + timedelta(minutes=2), 1.98, 2.09, 1.97, 2.07, 9000),
        ]
    )
    if second_break:
        bars.extend(
            [
                _bar(regular + timedelta(minutes=3), 2.07, 2.10, 1.98, 1.99, 6000),
                _bar(regular + timedelta(minutes=4), 1.99, 2.02, 1.97, 1.99, 5200),
                _bar(regular + timedelta(minutes=5), 1.99, 2.13, 1.98, 2.11, 9800),
            ]
        )
    return bars


def test_catalog_contains_momentum_v2_setups():
    ids = {row["strategy_id"] for row in strategy_catalog()}
    assert "PREMARKET_HIGH_BREAKOUT" in ids
    assert "BULL_FLAG_BREAKOUT" in ids
    assert "VWAP_PULLBACK_HOLD" in ids
    assert len(ids) == 9


def test_detects_premarket_high_breakout_and_context():
    result = detect_known_setups(_premarket_breakout_bars())
    ids = {row["strategy_id"] for row in result["matches"]}
    assert "PREMARKET_HIGH_BREAKOUT" in ids
    assert result["premarket_context"]["available"] is True
    assert result["premarket_context"]["high"] == 2.0

    match = next(row for row in result["matches"] if row["strategy_id"] == "PREMARKET_HIGH_BREAKOUT")
    assert match["instance_key"].startswith("PREMARKET_HIGH_BREAKOUT|")
    assert match["key_levels"]["premarket_high"] == 2.0
    assert match["plan_constraints"]["max_entry_extension_pct"] >= 5.0


def test_new_rebreak_creates_new_setup_instance():
    first = detect_known_setups(_premarket_breakout_bars())
    second = detect_known_setups(_premarket_breakout_bars(second_break=True))
    first_match = next(row for row in first["matches"] if row["strategy_id"] == "PREMARKET_HIGH_BREAKOUT")
    second_match = next(row for row in second["matches"] if row["strategy_id"] == "PREMARKET_HIGH_BREAKOUT")
    assert first_match["instance_key"] != second_match["instance_key"]
