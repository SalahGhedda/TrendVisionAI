from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class AppConfig:
    poll_interval_seconds: float = 0.35
    source_contains: str = "TrendVision"
    app_contains: str = "Discord"
    database_path: str = "data/trendvision.db"
    jsonl_path: str = "data/raw_notifications.jsonl"
    debug_log_path: str = "logs/uia_debug.log"


def load_config(path: str | Path | None = None) -> AppConfig:
    if path is None:
        return AppConfig()

    config_path = Path(path)
    if not config_path.exists():
        return AppConfig()

    data = json.loads(config_path.read_text(encoding="utf-8"))
    allowed = AppConfig.__dataclass_fields__.keys()
    return AppConfig(**{key: value for key, value in data.items() if key in allowed})
