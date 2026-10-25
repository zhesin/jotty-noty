"""Jotty REST API client and conversion of its responses into BoardState.

No single endpoint returns everything we need:
- GET /api/checklists?type=kanban lists owned and shared boards with createdBy/lastModifiedBy,
  but its items carry no assignee.
- GET /api/kanban/{boardId} returns assignee and the board's columns, but no createdBy/lastModifiedBy.
Cards from both are merged by card id. Both responses carry the board's updatedAt (the file's mtime),
which tells whether the board changed between the two calls.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Iterator

import httpx

from noty.models import BoardState, CardState

# Jotty normalizes the legacy "task" type to "kanban" when it parses a board, so one type covers all.
BOARD_TYPE = "kanban"

# The list endpoint takes a file's mtime from `find -printf %T@` (float seconds, truncated to
# milliseconds by new Date), the detail endpoint from fs.stat (rounded), so the same write can
# differ by 1 ms (source: app/_server/actions/checklist/readers.ts and queries.ts).
# BusyBox find has no -printf, so in the official Alpine image both use fs.stat and agree exactly.
MTIME_TOLERANCE = timedelta(milliseconds=1)


class JottyError(Exception):
    pass


class Unauthorized(JottyError):
    """The API key is missing, wrong or was regenerated."""


class BoardGone(JottyError):
    """The board disappeared or became invisible between the list and detail calls."""


class JottyClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: float = 15.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"x-api-key": api_key, "accept": "application/json"},
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> JottyClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _get(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        try:
            response = self._http.get(path, params=params)
        except httpx.HTTPError as exc:
            raise JottyError(f"GET {path} failed: {type(exc).__name__}: {exc}") from exc
        if response.status_code == 401:
            raise Unauthorized(f"GET {path}: 401")
        if response.status_code == 404:
            raise BoardGone(f"GET {path}: 404")
        if response.status_code != 200:
            raise JottyError(f"GET {path}: HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise JottyError(f"GET {path}: invalid JSON") from exc
        if not isinstance(data, dict):
            raise JottyError(f"GET {path}: unexpected response shape")
        return data

    def current_username(self) -> str:
        """Username of the API key owner (GET /api/user)."""
        user = self._get("/api/user").get("user")
        if not isinstance(user, dict) or not user.get("username"):
            raise JottyError("GET /api/user: missing username")
        return str(user["username"])

    def list_boards(self) -> list[dict[str, Any]]:
        """All Kanban boards the key owner owns or that were shared with them, items included."""
        data = self._get("/api/checklists", params={"type": BOARD_TYPE})
        return list(data.get("checklists", []))

    def get_board(self, board_id: str) -> dict[str, Any]:
        data = self._get(f"/api/kanban/{board_id}")
        board = data.get("board")
        if not isinstance(board, dict):
            raise JottyError(f"GET /api/kanban/{board_id}: missing board")
        return board


def _walk(
    items: list[dict[str, Any]],
    parents: tuple[str, ...] = (),
    parent_id: str | None = None,
) -> Iterator[tuple[dict[str, Any], tuple[str, ...], str | None]]:
    for item in items:
        yield item, parents, parent_id
        children = item.get("children") or []
        if children:
            yield from _walk(children, (*parents, item.get("text", "")), item["id"])


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def same_version(checklist: dict[str, Any], kanban_board: dict[str, Any]) -> bool:
    """Whether both responses describe the same write of the board file."""
    listed, detailed = checklist.get("updatedAt"), kanban_board.get("updatedAt")
    if listed == detailed:
        return True
    listed_at, detailed_at = _parse_time(listed), _parse_time(detailed)
    if listed_at is None or detailed_at is None:
        return False
    try:
        return abs(listed_at - detailed_at) <= MTIME_TOLERANCE
    except TypeError:  # one timestamp has a timezone and the other does not
        return False


def build_board_state(checklist: dict[str, Any], kanban_board: dict[str, Any]) -> BoardState:
    """Merge one board from /api/checklists with the same board from /api/kanban/{id}."""
    assignees = {item["id"]: item.get("assignee") for item, _, _ in _walk(kanban_board.get("items") or [])}

    cards: dict[str, CardState] = {}
    for item, parents, parent_id in _walk(checklist.get("items") or []):
        cards[item["id"]] = CardState(
            id=item["id"],
            text=item.get("text", ""),
            status=item.get("status"),
            completed=bool(item.get("completed", False)),
            description=item.get("description"),
            priority=item.get("priority"),
            assignee=assignees.get(item["id"]),
            target_date=item.get("targetDate"),
            created_by=item.get("createdBy"),
            last_modified_by=item.get("lastModifiedBy"),
            last_modified_at=item.get("lastModifiedAt"),
            parent_id=parent_id,
            parents=parents,
        )

    statuses = {
        status["id"]: status.get("label", status["id"])
        for status in kanban_board.get("statuses") or []
        if "id" in status
    }

    return BoardState(
        uuid=checklist["id"],
        title=checklist.get("title", ""),
        owner=checklist.get("owner"),
        statuses=statuses,
        cards=cards,
        updated_at=checklist.get("updatedAt"),
    )
