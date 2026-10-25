"""Builders for test data shaped like Jotty API responses and internal states."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from noty.models import BoardState, CardState

STATUSES = [
    {"id": "todo", "label": "To Do", "order": 0},
    {"id": "in_progress", "label": "In Progress", "order": 1},
    {"id": "completed", "label": "Completed", "order": 2},
]


T0 = "2026-10-06T09:00:00.000Z"
T1 = "2026-10-06T09:05:00.000Z"
T2 = "2026-10-06T09:06:00.000Z"


def card(card_id: str = "c1", **overrides: Any) -> CardState:
    fields: dict[str, Any] = {
        "id": card_id,
        "text": "Fix login bug",
        "status": "todo",
        "completed": False,
        "created_by": "alice",
        "last_modified_by": "alice",
        "last_modified_at": T0,
    }
    fields.update(overrides)
    return CardState(**fields)


def edited(base: CardState, by: str, at: str = T1, **changes: Any) -> CardState:
    """The card after `by` changed it through Jotty, which stamps lastModifiedBy/At."""
    return replace(base, last_modified_by=by, last_modified_at=at, **changes)


_EMPTY: Any = object()


def board(*cards: CardState, uuid: str = "b1", title: str = "Sprint 12", comment_ids: set[str] | None = _EMPTY) -> BoardState:
    return BoardState(
        uuid=uuid,
        title=title,
        owner="alice",
        statuses={status["id"]: status["label"] for status in STATUSES},
        cards={c.id: c for c in cards},
        comment_ids=set() if comment_ids is _EMPTY else comment_ids,
    )


def api_checklist(uuid: str = "b1", items: list[dict[str, Any]] | None = None, **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": uuid,
        "title": "Sprint 12",
        "category": "Uncategorized",
        "type": "kanban",
        "owner": "alice",
        "isShared": False,
        "items": items or [],
        "updatedAt": T0,
    }
    data.update(overrides)
    return data


def api_item(item_id: str, text: str = "Fix login bug", **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": item_id,
        "index": 0,
        "itemIndex": "0",
        "text": text,
        "completed": False,
        "status": "todo",
        "createdBy": "alice",
        "lastModifiedBy": "alice",
        "lastModifiedAt": T0,
    }
    data.update(overrides)
    return data


def api_board(uuid: str = "b1", items: list[dict[str, Any]] | None = None, **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": uuid,
        "title": "Sprint 12",
        "category": "Uncategorized",
        "statuses": STATUSES,
        "items": items or [],
        "updatedAt": T0,
    }
    data.update(overrides)
    return data


def kanban_item(item_id: str, assignee: str | None = None, children: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {"id": item_id, "index": 0, "text": "x", "status": "todo", "completed": False}
    if assignee:
        data["assignee"] = assignee
    if children:
        data["children"] = children
    return data
