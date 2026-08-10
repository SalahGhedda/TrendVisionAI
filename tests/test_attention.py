from trendvision_ai.attention import evaluate_attention


def test_multi_channel_convergence_outranks_single_alert():
    single = evaluate_attention({
        "ticker": "AAA",
        "channels": ["news-scanner"],
        "events": [
            {"channel": "news-scanner", "data": {}, "headline": "news"},
        ],
    })
    converged = evaluate_attention({
        "ticker": "BBB",
        "channels": ["news-scanner", "volume-scanner", "all-in-one-scanner"],
        "events": [
            {"channel": "news-scanner", "data": {}, "headline": "news"},
            {"channel": "volume-scanner", "data": {"change_pct": 12.0}, "headline": "volume"},
            {"channel": "all-in-one-scanner", "data": {"signal": "MOMENTUM", "relative_volume": "8x"}, "headline": "momentum"},
        ],
    })

    assert converged.score > single.score
    assert converged.channel_count == 3
    assert converged.tier in {"REVIEW", "HIGH ATTENTION"}


def test_repeated_same_channel_is_capped():
    result = evaluate_attention({
        "ticker": "AAA",
        "channels": ["volume-scanner"],
        "events": [
            {"channel": "volume-scanner", "data": {}, "headline": str(i)}
            for i in range(10)
        ],
    })
    assert result.score == 4  # channel + capped repeat bonus


def test_whale_drop_is_a_risk_flag_not_positive_score():
    result = evaluate_attention({
        "ticker": "JWEL",
        "channels": ["whale-scanner"],
        "events": [
            {"channel": "whale-scanner", "data": {"direction": "down"}, "headline": "Price is dropping"},
        ],
    })
    assert result.score == 1
    assert "price is dropping" in result.risk_flags[0]
