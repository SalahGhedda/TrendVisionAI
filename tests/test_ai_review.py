from trendvision_ai.ai_review import build_review_snapshot


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
