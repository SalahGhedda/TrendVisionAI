from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(slots=True)
class CapturedNotification:
    received_at: str
    app_name: str
    source: str
    channel: str | None
    title: str | None
    body: str
    raw_text: str
    ticker: str | None
    fingerprint: str

    @classmethod
    def now(
        cls,
        *,
        app_name: str,
        source: str,
        channel: str | None,
        title: str | None,
        body: str,
        raw_text: str,
        ticker: str | None,
        fingerprint: str,
    ) -> "CapturedNotification":
        return cls(
            received_at=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            app_name=app_name,
            source=source,
            channel=channel,
            title=title,
            body=body,
            raw_text=raw_text,
            ticker=ticker,
            fingerprint=fingerprint,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
