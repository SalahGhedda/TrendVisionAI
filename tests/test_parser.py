from trendvision_ai.parser import parse_uia_texts


def test_parse_social_news_notification_from_screenshot_shape():
    parsed = parse_uia_texts(
        [
            "Discord",
            "TrendVision (#social-news, Trend Vision Scanner)",
            "Check this out.",
            "Market Maps lets you see where the market is moving at a glance, making it easier to spot trends.",
        ]
    )

    assert parsed is not None
    assert parsed.channel == "social-news"
    assert parsed.title == "Check this out."
    assert parsed.ticker is None
    assert "Market Maps" in parsed.body


def test_parse_scanner_ticker():
    parsed = parse_uia_texts(
        [
            "Discord",
            "TrendVision (#volume-scanner, Trend Vision Scanner)",
            "XHLD - Alert #6 | +6.41%",
            "Price $2.99 MCap 33.66M Float 7.79M",
            "Rel Vol 10.33x",
        ]
    )

    assert parsed is not None
    assert parsed.channel == "volume-scanner"
    assert parsed.ticker == "XHLD"


def test_requires_trendvision():
    assert parse_uia_texts(["Discord", "Friend", "hello"]) is None
