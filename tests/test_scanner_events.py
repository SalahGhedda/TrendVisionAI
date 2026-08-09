from trendvision_ai.models import CapturedNotification
from trendvision_ai.scanner_events import CHANNEL_SCHEMAS, build_scanner_events


def notification(channel: str, body: str, ticker: str | None = None) -> CapturedNotification:
    return CapturedNotification.now(
        app_name="Discord",
        source="TrendVision",
        channel=channel,
        title=None,
        body=body,
        raw_text=f"Discord\nTrendVision (#{channel}, Trend Vision Scanner)\n{body}",
        ticker=ticker,
        fingerprint=f"fp-{channel}-{body[:20]}",
    )


def test_channel_contracts_cover_all_user_supplied_formats():
    assert set(CHANNEL_SCHEMAS) == {
        "all-in-one-scanner",
        "social-news",
        "news-scanner",
        "volume-scanner",
        "whale-scanner",
        "potential-squeeze-alerts",
        "0-borrow-scanner",
        "halt-scanner",
        "ipo-scanner",
    }


def test_social_news_is_body_only():
    event = build_scanner_events(notification("social-news", "General market headline"))[0]
    assert event.ticker is None
    assert event.data == {"body": "General market headline"}


def test_news_scanner_separates_ticker_from_headline():
    event = build_scanner_events(notification(
        "news-scanner",
        "TZO :flag_us: Club Offers for Travel Enthusiasts in Germany",
        "TZO",
    ))[0]
    assert event.ticker == "TZO"
    assert event.data["headline"] == "Club Offers for Travel Enthusiasts in Germany"


def test_volume_scanner_contract():
    event = build_scanner_events(notification(
        "volume-scanner",
        "XHLD - Alert #6 ↑ 6.41%\nPrice $2.99 MCap 33.66M Float 7.79M\nMin Vol 21.00K Rel Vol 10.33x Mon Vol $62.80K\nFORM 424B3\nAlert Reason REV V",
        "XHLD",
    ))[0]
    assert event.data["alert_number"] == 6
    assert event.data["change_pct"] == 6.41
    assert event.data["relative_volume"] == "10.33x"


def test_whale_scanner_does_not_use_heading_as_headline_data():
    event = build_scanner_events(notification(
        "whale-scanner",
        "Small Whale Alert\nOXSQ\nPrice is dropping ↓\nPrice 1.37 Shares 750.00K Order Value $1.03M\nFloat: 105.06M (0.71%)\nMarket Cap: 162.84M (0.63%)",
        "OXSQ",
    ))[0]
    assert event.ticker == "OXSQ"
    assert event.data["direction"] == "down"
    assert event.data["order_value"] == "1.03M"


def test_potential_squeeze_has_zero_borrow_flag():
    event = build_scanner_events(notification(
        "potential-squeeze-alerts",
        "Potential Squeeze\nBIVI - Alert #2 ↓ 11.11%\nPrice $1.21 MCap 10.18M Float 7.19M\nMin Vol 82.95K Rel Vol 64.12x Mon Vol $100.37K\n0 Borrow\nNo shares available to borrow\nAlert Reason MOMENTUM",
        "BIVI",
    ))[0]
    assert event.data["zero_borrow"] is True
    assert event.data["alert_number"] == 2


def test_zero_borrow_contract():
    event = build_scanner_events(notification(
        "0-borrow-scanner",
        "TrendVision\nSGLY\nStock currently has no shares available to borrow\nMarket Cap 6.8M CTB Fee 258.04% Short Int 13.19%",
        "SGLY",
    ))[0]
    assert event.data["no_shares_available"] is True
    assert event.data["ctb_fee_pct"] == "258.04%"


def test_halt_contract():
    event = build_scanner_events(notification(
        "halt-scanner",
        "TrendVision\nMB (HALTED UP)\nPrice: $8.92\nChange: ↑137.87%\nVolume: 31.4M",
        "MB",
    ))[0]
    assert event.data["halt_status"] == "HALTED UP"
    assert event.data["volume"] == "31.4M"


def test_ipo_does_not_mistake_new_for_ticker():
    event = build_scanner_events(notification(
        "ipo-scanner",
        "Initial Public Offerings Scanner\nNew upcoming IPO\nSymbol Exchange List Date\nSCATU NASDAQ 2026-08-12\nStock Data\nName: Southern Cross Acquisition II Corp.\nPrice Range: 10.00\nIssued Shares: N/A",
        None,
    ))[0]
    assert event.ticker == "SCATU"
    assert event.data["exchange"] == "NASDAQ"
    assert event.data["status"] == "new"


def test_all_in_one_can_create_multiple_ticker_events():
    events = build_scanner_events(notification(
        "all-in-one-scanner",
        "ZJYL #6 · MOMENTUM · ↑128% · $4.37 · FT 1.8M · MC 15M · RV 42x · 1V 157K\n"
        "ZJYL #7 · REV V · ↑126% · $4.36 · FT 1.8M · MC 15M · RV 21x · 1V 80K\n"
        "0 BORROW · CTB 34.31% · SI 0.88%\n"
        "SCKT #1 · ↑15% · $0.470 · FT 5.4M · MC 3.2M · RV 2x · 1V 5K",
        "ZJYL",
    ))
    assert [event.ticker for event in events] == ["ZJYL", "ZJYL", "SCKT"]
    assert events[1].data["zero_borrow"] is True
    assert events[1].data["ctb_fee_pct"] == "34.31%"
