from trendvision_ai.ticker_memory import (
    TickerEventRecord,
    build_ticker_state,
    convergence_summary,
)


def event(time: str, channel: str, headline: str, **data):
    return TickerEventRecord(
        received_at=time,
        channel=channel,
        event_type=channel.replace("-", "_"),
        headline=headline,
        data=data,
    )


def test_ticker_state_merges_latest_non_empty_facts():
    events = [
        event(
            "2026-08-10T09:00:00-04:00",
            "all-in-one-scanner",
            "LRHC momentum",
            ticker="LRHC",
            price="0.996",
            signal="MOMENTUM",
            relative_volume="26x",
        ),
        event(
            "2026-08-10T09:05:00-04:00",
            "volume-scanner",
            "LRHC Alert #2 +18.78%",
            ticker="LRHC",
            change_pct=18.78,
            price=None,
        ),
    ]

    state = build_ticker_state("LRHC", events)
    assert state is not None
    assert state.event_count == 2
    assert state.channel_count == 2
    assert state.channels == ["all-in-one-scanner", "volume-scanner"]
    assert state.facts["price"]["value"] == "0.996"
    assert state.facts["change_pct"]["value"] == 18.78
    assert state.facts["signal"]["source_channel"] == "all-in-one-scanner"


def test_recent_convergence_does_not_mix_old_alerts():
    events = [
        event("2026-08-10T08:00:00-04:00", "news-scanner", "Old news"),
        event("2026-08-10T09:00:00-04:00", "all-in-one-scanner", "Momentum"),
        event("2026-08-10T09:10:00-04:00", "volume-scanner", "Volume alert"),
        event("2026-08-10T09:20:00-04:00", "potential-squeeze-alerts", "Squeeze alert"),
    ]

    summary = convergence_summary("LRHC", events, window_minutes=30)
    assert summary["event_count"] == 3
    assert summary["channel_count"] == 3
    assert summary["channels"] == [
        "all-in-one-scanner",
        "volume-scanner",
        "potential-squeeze-alerts",
    ]
