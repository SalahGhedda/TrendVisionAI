from trendvision_ai.ai_review import build_review_snapshot, calibrate_review_payload


def test_review_snapshot_keeps_missing_fields_missing():
    state = {
        "first_seen_at": "2026-08-10T09:00:00-04:00",
        "last_seen_at": "2026-08-10T09:10:00-04:00",
        "event_count": 2,
        "channel_count": 2,
        "channels": ["all-in-one-scanner", "volume-scanner"],
        "latest_event_type": "volume",
        "latest_headline": "LRHC Alert #2 +18.78%",
        "facts": {
            "price": {
                "value": "0.996",
                "source_channel": "all-in-one-scanner",
                "received_at": "2026-08-10T09:00:00-04:00",
            },
            "relative_volume": {
                "value": "26x",
                "source_channel": "all-in-one-scanner",
                "received_at": "2026-08-10T09:00:00-04:00",
            },
        },
    }
    convergence = {
        "window_minutes": 30,
        "event_count": 2,
        "channel_count": 2,
        "channels": ["all-in-one-scanner", "volume-scanner"],
        "events": [
            {
                "received_at": "2026-08-10T09:00:00-04:00",
                "channel": "all-in-one-scanner",
                "event_type": "all_in_one",
                "headline": "LRHC MOMENTUM",
                "data": {"signal": "MOMENTUM", "relative_volume": "26x"},
            },
            {
                "received_at": "2026-08-10T09:10:00-04:00",
                "channel": "volume-scanner",
                "event_type": "volume",
                "headline": "LRHC Alert #2 +18.78%",
                "data": {"change_pct": 18.78},
            },
        ],
    }
    attention = {"ticker": "LRHC", "score": 9, "tier": "REVIEW"}

    snapshot = build_review_snapshot(
        ticker="LRHC",
        state=state,
        convergence=convergence,
        attention=attention,
    )

    facts = snapshot["ticker_memory"]["latest_known_facts"]
    assert facts["price"]["value"] == "0.996"
    assert facts["relative_volume"]["value"] == "26x"
    assert "market_cap" not in facts
    assert snapshot["recent_convergence"]["events"][1]["data"] == {"change_pct": 18.78}
    assert any("must not be inferred" in value for value in snapshot["important_limitations"])
    assert snapshot["review_version"] == 2


def test_extreme_runner_is_not_left_as_clean_potential_setup():
    snapshot = {
        "recent_convergence": {
            "events": [
                {
                    "channel": "all-in-one-scanner",
                    "headline": "WYHG breakout",
                    "data": {
                        "change_pct": 125.0,
                        "signal": "BREAKOUT",
                        "relative_volume": "39x",
                    },
                },
                {
                    "channel": "halt-scanner",
                    "headline": "WYHG (HALTED UP)",
                    "data": {"halt_status": "HALTED UP"},
                },
                {
                    "channel": "halt-scanner",
                    "headline": "WYHG (HALTED DOWN)",
                    "data": {"halt_status": "HALTED DOWN"},
                },
            ]
        }
    }
    parsed = {
        "interest_level": "VERY HIGH",
        "risk_level": "MODERATE",
        "evidence_quality": "HIGH",
        "review_status": "POTENTIAL SETUP",
        "summary": "Interesting runner",
        "positive_factors": ["BREAKOUT", "Known Runner label"],
        "risk_factors": ["China flag / foreign issuer risk", "Repeated halts"],
        "data_conflicts": [],
        "missing_information": [],
        "next_signals_to_watch": [],
        "invalidation_warnings": [],
    }

    result = calibrate_review_payload(parsed, snapshot)

    assert result["interest_level"] == "VERY HIGH"
    assert result["risk_level"] == "EXTREME"
    assert result["review_status"] == "WAIT FOR CONFIRMATION"
    assert "Known Runner label" not in result["positive_factors"]
    assert "China flag / foreign issuer risk" not in result["risk_factors"]
    assert "Repeated halts" in result["risk_factors"]


def test_structured_borrow_conflict_caps_evidence_quality():
    snapshot = {
        "recent_convergence": {
            "events": [
                {
                    "channel": "all-in-one-scanner",
                    "headline": "TEST #1",
                    "data": {
                        "zero_borrow": False,
                        "raw_payload": "TEST #1\n0 Borrow",
                    },
                }
            ]
        }
    }
    parsed = {
        "interest_level": "HIGH",
        "risk_level": "MODERATE",
        "evidence_quality": "HIGH",
        "review_status": "POTENTIAL SETUP",
        "summary": "Conflicting borrow data",
        "positive_factors": [],
        "risk_factors": [],
        "data_conflicts": [],
        "missing_information": [],
        "next_signals_to_watch": [],
        "invalidation_warnings": [],
    }

    result = calibrate_review_payload(parsed, snapshot)

    assert result["evidence_quality"] == "MEDIUM"
    assert result["review_status"] == "WAIT FOR CONFIRMATION"
    assert any("zero_borrow" in value for value in result["data_conflicts"])


def test_single_halt_sets_at_least_high_risk():
    snapshot = {
        "recent_convergence": {
            "events": [
                {
                    "channel": "halt-scanner",
                    "headline": "ABC (HALTED)",
                    "data": {"halt_status": "HALTED"},
                }
            ]
        }
    }
    parsed = {
        "interest_level": "MEDIUM",
        "risk_level": "LOW",
        "evidence_quality": "MEDIUM",
        "review_status": "WATCH",
        "summary": "Halted",
        "positive_factors": [],
        "risk_factors": [],
        "data_conflicts": [],
        "missing_information": [],
        "next_signals_to_watch": [],
        "invalidation_warnings": [],
    }

    result = calibrate_review_payload(parsed, snapshot)

    assert result["risk_level"] == "HIGH"
    assert result["review_status"] == "WATCH"
