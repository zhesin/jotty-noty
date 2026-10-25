"""Container health check.

The notifier touches a heartbeat file after every successful user poll and every delivered
message, so the heartbeat goes stale when no user can be polled (Jotty down, keys rejected)
but not during a long round of messages. Exits 0 when the heartbeat is fresh, 1 otherwise.
Reads only POLL_INTERVAL and STATE_DIR, so it works without the full settings.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from noty.state import HEARTBEAT_FILE

# Allowed age: a few poll rounds plus slack for slow Jotty responses and Telegram rate limits.
GRACE_SECONDS = 300


def main() -> int:
    try:
        interval = int(os.environ.get("POLL_INTERVAL") or 60)
    except ValueError:
        interval = 60
    heartbeat = Path(os.environ.get("STATE_DIR", "/app/state")) / HEARTBEAT_FILE
    try:
        age = time.time() - heartbeat.stat().st_mtime
    except OSError:
        print(f"No heartbeat at {heartbeat}", file=sys.stderr)
        return 1
    if age > 3 * interval + GRACE_SECONDS:
        print(f"Heartbeat is {age:.0f}s old", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
