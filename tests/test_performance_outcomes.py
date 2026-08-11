from __future__ import annotations

from trendvision_ai.automatic_outcomes import AutomaticOutcomeStore, SCOPE_MARKET_SESSION
from trendvision_ai.performance_outcomes import install_outcome_performance_patches


install_outcome_performance_patches()


def test_completed_market_horizons_are_not_recomputed_forever(tmp_path) -> None:
    store = AutomaticOutcomeStore(tmp_path / "trendvision.db")
    store.market_store.list_sessions = lambda limit=200: [  # type: ignore[method-assign]
        {
            "id": 1,
            "ticker": "ABCD",
            "started_at": "2026-08-11T09:30:00-04:00",
            "reference_captured_at": "2026-08-11T09:30:00-04:00",
        }
    ]

    calls: list[int] = []

    def fake_metrics(*, session_id: int, horizon_minutes: int):
        calls.append(int(horizon_minutes))
        return {
            "available": True,
            "target_minutes": int(horizon_minutes),
            "horizon_complete": True,
            "fresh_to_horizon": True,
            "coverage_pct": 100.0,
            "sample_count": 20,
            "reference_captured_at": "2026-08-11T09:30:00-04:00",
            "return_pct": 3.0,
            "mfe_pct": 5.0,
            "mae_pct": -1.0,
            "max_spread_pct": 1.0,
        }

    store.market_store.session_horizon_metrics = fake_metrics  # type: ignore[method-assign]

    first = store.refresh_due_session_outcomes(limit=20)
    assert first == 4
    assert len(calls) == 4
    assert store.count_outcomes(scope=SCOPE_MARKET_SESSION) == 4

    calls.clear()
    second = store.refresh_due_session_outcomes(limit=20)
    assert second == 0
    assert calls == []
