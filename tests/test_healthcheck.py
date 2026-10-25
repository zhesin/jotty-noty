import os
import time

from noty.healthcheck import GRACE_SECONDS, main
from noty.state import HEARTBEAT_FILE


def test_missing_heartbeat_is_unhealthy(monkeypatch, tmp_path):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    assert main() == 1


def test_fresh_heartbeat_is_healthy(monkeypatch, tmp_path):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    (tmp_path / HEARTBEAT_FILE).touch()
    assert main() == 0


def test_empty_or_bad_poll_interval_uses_default(monkeypatch, tmp_path):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    (tmp_path / HEARTBEAT_FILE).touch()
    for value in ("", "abc"):
        monkeypatch.setenv("POLL_INTERVAL", value)
        assert main() == 0


def test_stale_heartbeat_is_unhealthy(monkeypatch, tmp_path):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.setenv("POLL_INTERVAL", "60")
    heartbeat = tmp_path / HEARTBEAT_FILE
    heartbeat.touch()
    old = time.time() - (3 * 60 + GRACE_SECONDS + 1)
    os.utime(heartbeat, (old, old))
    assert main() == 1
