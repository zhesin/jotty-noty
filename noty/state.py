"""Per-user snapshot of boards, persisted as JSON between polls and restarts."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

from noty.models import BoardState

log = logging.getLogger(__name__)

STATE_VERSION = 2
HEARTBEAT_FILE = ".heartbeat"


class StateStore:
    def __init__(self, directory: Path) -> None:
        self._dir = directory

    def _path(self, username: str) -> Path:
        return self._dir / f"{username}.json"

    def load(self, username: str) -> dict[str, BoardState] | None:
        """Saved boards, or None when there is no usable snapshot (first run or unreadable file)."""
        path = self._path(username)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("version") != STATE_VERSION:
                log.warning("State file %s has another version, starting a new baseline", path)
                return None
            return {uuid: BoardState.from_dict(board) for uuid, board in data["boards"].items()}
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            log.warning("Cannot load state %s (%s), starting a new baseline", path, exc)
            return None

    def save(self, username: str, boards: dict[str, BoardState]) -> None:
        """Write atomically and durably so a crash never leaves a half-written or empty snapshot."""
        self._dir.mkdir(parents=True, exist_ok=True)
        payload = {"version": STATE_VERSION, "boards": {uuid: board.to_dict() for uuid, board in boards.items()}}
        fd, tmp = tempfile.mkstemp(dir=self._dir, prefix=f".{username}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self._path(username))
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        self._fsync_dir()

    def _fsync_dir(self) -> None:
        # Persist the rename itself; not every filesystem allows opening a directory.
        try:
            fd = os.open(self._dir, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(fd)
        except OSError:
            pass
        finally:
            os.close(fd)

    def touch_heartbeat(self) -> None:
        """Mark that the poll loop is alive; read by `python -m noty.healthcheck`."""
        self._dir.mkdir(parents=True, exist_ok=True)
        (self._dir / HEARTBEAT_FILE).touch()
