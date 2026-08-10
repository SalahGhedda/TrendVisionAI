from trendvision_ai.review_calibration import _normalize_snapshot, calibrate_v3


def test_v3_treats_time_series_changes_as_updates_not_conflicts():
    snapshot = {
        "ticker": "WYHG",
        "ticker_memory": {
            "latest_known_facts": {
                "zero_borrow": {
                    "value": False,
                    "source_channel": "all-in-one-scanner",
                    "received_at": "2026-08-10T11:50:00-04:00",
                }
            }
        },
        "recent_convergence": {
            "events": [
                {
                    "received_at": "2026-08-10T11:50:00-04:00",
                    "channel": "halt-scanner",
                    "headline": "WYHG (HALTED UP)",
                    "data": {"halt_status": "HALTED UP", "change_pct": 126.0},
                },
                {
                    "received_at": "2026-08-10T11:55:00-04:00",
                    "channel": "halt-scanner",
                    "headline": "WYHG (HALTED DOWN)",
                    "data": {"halt_status": "HALTED DOWN", "change_pct": 170.0},
                },
                {
                    "received_at": "2026-08-10T11:57:00-04:00",
                    "channel": "all-in-one-scanner",
                    "headline": "WYHG #9 REV S",
                    "data": {"price": "10.71", "relative_volume": "120x", "zero_borrow": False},
                },
            ]
        },
    }
    normalized = _normalize_snapshot(snapshot)
    assert "zero_borrow" not in normalized["ticker_memory"]["latest_known_facts"]
    assert "zero_borrow" not in normalized["recent_convergence"]["events"][2]["data"]

    parsed = {
        "interest_level": "VERY HIGH",
        "risk_level": "HIGH",
        "evidence_quality": "HIGH",
        "review_status": "POTENTIAL SETUP",
        "summary": "High-interest runner.",
        "positive_factors": ["High RV", "Known Runner label"],
        "risk_factors": ["Repeated halts", "Known Runner implies pump risk"],
        "data_conflicts": [
            "Price/change values differ across alerts at different times.",
            "Halt status toggles between HALTED UP and HALTED DOWN across the sequence.",
            "Structured field conflicts with raw text in the same alert.",
        ],
        "missing_information": ["Options activity", "Catalyst/news"],
        "next_signals_to_watch": ["Level 2 order book", "Another volume-scanner alert"],
        "invalidation_warnings": ["Immediate collapse after the next observed continuation"],
    }

    result = calibrate_v3(parsed, normalized)
    assert result["risk_level"] == "EXTREME"
    assert result["review_status"] == "WAIT FOR CONFIRMATION"
    assert result["data_conflicts"] == ["Structured field conflicts with raw text in the same alert."]
    assert result["positive_factors"] == ["High RV"]
    assert result["risk_factors"] == ["Repeated halts"]
    assert result["missing_information"] == ["Catalyst/news"]
    assert result["next_signals_to_watch"] == ["Another volume-scanner alert"]
