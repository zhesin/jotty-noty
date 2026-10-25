"""Read Kanban card comments straight from Jotty's data directory.

Jotty's REST API does not expose comments. They live in
data/checklists/<owner>/.comments/<boardUuid>.json as {"items": {<cardId>: [comment, ...]}}.
The directory is mounted read-only; this module never writes to it.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from noty.models import Comment

log = logging.getLogger(__name__)


def is_safe_segment(segment: str) -> bool:
    """Same rule as Jotty's validateNoPathTraversal, plus non-empty and not '.'."""
    return bool(segment) and segment != "." and ".." not in segment and "/" not in segment and "\\" not in segment


def comments_file(data_dir: Path, owner: str, board_uuid: str) -> Path | None:
    if not (is_safe_segment(owner) and is_safe_segment(board_uuid)):
        return None
    return data_dir / "checklists" / owner / ".comments" / f"{board_uuid}.json"


def read_comments(data_dir: Path, owner: str | None, board_uuid: str) -> list[Comment] | None:
    """Return the board's comments, [] when it has none, or None when they cannot be read."""
    if not owner:
        return None
    path = comments_file(data_dir, owner, board_uuid)
    if path is None:
        log.warning("Refusing unsafe comments path for owner=%r board=%r", owner, board_uuid)
        return None
    # Jotty always creates the owner's checklists directory. Without it the mount is wrong or empty,
    # and answering [] would record an empty baseline that later turns every old comment into "new".
    if not path.parent.parent.is_dir():
        return None
    if not path.exists():
        return []

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("Cannot read comments file %s: %s", path, exc)
        return None

    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, dict):
        return []

    comments: list[Comment] = []
    for card_id, entries in items.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict) or "id" not in entry:
                continue
            comments.append(
                Comment(
                    id=str(entry["id"]),
                    card_id=card_id,
                    author=str(entry.get("author", "")),
                    text=str(entry.get("text", "")),
                    created_at=str(entry.get("createdAt", "")),
                )
            )
    comments.sort(key=lambda comment: comment.created_at)
    return comments
