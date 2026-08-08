from trendvision_ai.models import CapturedNotification
from trendvision_ai.storage import AlertStore


def sample_notification(fingerprint: str = "abc") -> CapturedNotification:
    return CapturedNotification(
        received_at="2026-08-08T12:00:00-04:00",
        app_name="Discord",
        source="TrendVision",
        channel="volume-scanner",
        title="XHLD - Alert #6",
        body="Price $2.99",
        raw_text="Discord\\nTrendVision\\nXHLD - Alert #6\\nPrice $2.99",
        ticker="XHLD",
        fingerprint=fingerprint,
    )


def test_store_deduplicates(tmp_path):
    store = AlertStore(tmp_path / "alerts.db", tmp_path / "alerts.jsonl")
    notification = sample_notification()

    assert store.save(notification) is True
    assert store.save(notification) is False
    assert len((tmp_path / "alerts.jsonl").read_text(encoding="utf-8").splitlines()) == 1
