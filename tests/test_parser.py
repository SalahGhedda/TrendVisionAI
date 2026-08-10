from trendvision_ai.parser import extract_channel_from_header, parse_uia_texts


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
    assert parsed.title is None
    assert parsed.ticker is None
    assert parsed.body == (
        "Check this out.\n"
        "Market Maps lets you see where the market is moving at a glance, making it easier to spot trends."
    )


def test_parse_winrt_social_news_payload():
    parsed = parse_uia_texts(
        [
            "Discord",
            "\ufeffTrendVision (#social-news, Trend Vision Scanner)",
            "Prices of all food categories are up 29%, per CNBC",
        ]
    )

    assert parsed is not None
    assert parsed.channel == "social-news"
    assert parsed.title is None
    assert parsed.ticker is None
    assert parsed.body == "Prices of all food categories are up 29%, per CNBC"


def test_parse_channel_with_invisible_bidi_controls():
    header = "\u2066TrendVision\u2069 (\u200e#social-news\u200f, Trend Vision Scanner)"
    assert extract_channel_from_header(header) == "social-news"

    parsed = parse_uia_texts(
        [
            "Discord",
            header,
            "29% of buy now, pay later users said they've used these short-term loans to buy groceries.",
        ]
    )
    assert parsed is not None
    assert parsed.channel == "social-news"
    assert parsed.title is None
    assert parsed.ticker is None


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


def test_normalizes_markdown_from_real_all_in_one_toast():
    parsed = parse_uia_texts(
        [
            "Discord",
            "TrendVision (#all-in-one-scanner, Trend Vision Scanner)",
            "**NWC** :flag_us: · #1 · ↑6% · $1.52 · **FT** 25M · **MC** 36M · **RV** 0.90x · **1V** 2K",
            "> NEWS • NanoViricides Has Received Regulatory Approval for a Phase II Clinical Trial",
        ]
    )

    assert parsed is not None
    assert parsed.channel == "all-in-one-scanner"
    assert parsed.ticker is None  # one all-in-one toast may contain several symbols
    assert parsed.body.startswith("NWC :flag_us: · #1 · ↑6% · $1.52 · FT 25M · MC 36M · RV 0.90x · 1V 2K")


def test_requires_trendvision():
    assert parse_uia_texts(["Discord", "Friend", "hello"]) is None


def test_rejects_non_discord_window_containing_project_name():
    assert (
        parse_uia_texts(
            [
                "TrendVisionAI - Visual Studio Code",
                "C:\\Projects\\TrendVisionAI",
                "README.md",
                "TrendVision notification listener",
            ]
        )
        is None
    )


def test_rejects_discord_text_without_real_scanner_header():
    assert (
        parse_uia_texts(
            [
                "Discord",
                "TrendVisionAI",
                "scripts\\run_listener.bat",
            ]
        )
        is None
    )
