from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

CHANNEL_RE = re.compile(r"TrendVision\s*\(\s*#(?P<channel>[^,\)]+)", re.IGNORECASE)


def _channel_from_lines(lines: list[str]) -> str:
    for line in lines:
        match = CHANNEL_RE.search(str(line))
        if match:
            return match.group("channel").strip()
    return "unknown"


def main() -> int:
    path = Path("data/winrt_candidates.jsonl")
    if not path.exists():
        print("No data/winrt_candidates.jsonl file yet.")
        print("Leave scripts\\run_notification_api_listener.bat running until some alerts arrive.")
        return 0

    records: list[dict] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            records.append(json.loads(raw))
        except json.JSONDecodeError:
            continue

    if not records:
        print("The candidate file exists, but it contains no readable records yet.")
        return 0

    counts: Counter[str] = Counter()
    examples: dict[str, list[dict]] = defaultdict(list)

    for record in records:
        lines = [str(x) for x in record.get("lines", [])]
        channel = _channel_from_lines(lines)
        counts[channel] += 1
        if len(examples[channel]) < 2:
            examples[channel].append(record)

    print("TrendVisionAI - Captured WinRT Scanner Summary")
    print("=" * 72)
    print(f"Total captured candidate notifications: {len(records)}")
    print()
    print("Channels seen:")
    for channel, count in counts.most_common():
        print(f"  #{channel}: {count}")

    print("\nRepresentative payloads (max 2 per channel):")
    print("=" * 72)
    for channel in sorted(examples):
        print(f"\n#{channel}")
        for i, record in enumerate(examples[channel], start=1):
            print(f"--- example {i} ---")
            print(f"App: {record.get('app_name', '<unknown>')}  ID: {record.get('notification_id', '?')}")
            lines = [str(x) for x in record.get("lines", [])]
            if lines:
                for line in lines:
                    print(line)
            else:
                print("(no text lines)")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
