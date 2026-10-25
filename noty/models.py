"""Normalized board/card/comment data shared by the fetcher, differ and state store."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


@dataclass(frozen=True)
class CardState:
    id: str
    text: str
    status: str | None = None
    completed: bool = False
    description: str | None = None
    priority: str | None = None
    assignee: str | None = None
    target_date: str | None = None
    created_by: str | None = None
    last_modified_by: str | None = None
    last_modified_at: str | None = None
    parent_id: str | None = None
    # Texts of ancestor cards, outermost first; empty for top-level cards.
    parents: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["parents"] = list(self.parents)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CardState:
        return cls(**{**data, "parents": tuple(data.get("parents", ()))})


@dataclass
class BoardState:
    uuid: str
    title: str
    owner: str | None
    # Status id -> column label.
    statuses: dict[str, str] = field(default_factory=dict)
    cards: dict[str, CardState] = field(default_factory=dict)
    # None means comments could not be read yet, so nothing is known about them.
    comment_ids: set[str] | None = None
    # Set while the board is absent from the API (archived, unshared, read error); ISO timestamp.
    missing_since: str | None = None
    # The board's updatedAt from the API, which is the board file's mtime.
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "uuid": self.uuid,
            "title": self.title,
            "owner": self.owner,
            "statuses": self.statuses,
            "cards": {card_id: card.to_dict() for card_id, card in self.cards.items()},
            "comment_ids": sorted(self.comment_ids) if self.comment_ids is not None else None,
            "missing_since": self.missing_since,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BoardState:
        comment_ids = data.get("comment_ids")
        return cls(
            uuid=data["uuid"],
            title=data["title"],
            owner=data.get("owner"),
            statuses=dict(data.get("statuses", {})),
            cards={card_id: CardState.from_dict(card) for card_id, card in data.get("cards", {}).items()},
            comment_ids=set(comment_ids) if comment_ids is not None else None,
            missing_since=data.get("missing_since"),
            updated_at=data.get("updated_at"),
        )

    def root_of(self, card: CardState) -> CardState:
        """Top-level ancestor of a card (the card itself when it is top-level)."""
        seen = {card.id}
        while card.parent_id and card.parent_id in self.cards and card.parent_id not in seen:
            card = self.cards[card.parent_id]
            seen.add(card.id)
        return card


@dataclass(frozen=True)
class Comment:
    id: str
    card_id: str
    author: str
    text: str
    created_at: str


class EventKind(str, Enum):
    ASSIGNED = "assigned"
    COMPLETED = "completed"
    UPDATED = "updated"
    COMMENT = "comment"
    MENTION = "mention"
    DELETED = "deleted"
    BOARD_GONE = "board_gone"


@dataclass(frozen=True)
class Change:
    """A changed field; old and new both None means "changed" without showing values."""

    field: str
    old: str | None
    new: str | None


@dataclass(frozen=True)
class SubtaskChanges:
    """Changes of one sub-card folded into its top-level card's message."""

    # Card texts below the top-level card, ending with the sub-card's own text.
    path: tuple[str, ...]
    changes: tuple[Change, ...] = ()


@dataclass(frozen=True)
class Event:
    kind: EventKind
    board_uuid: str
    board_title: str
    # Ancestor card texts followed by the card's own text.
    card_path: tuple[str, ...]
    # None when Jotty changed the card without recording who did it.
    actor: str | None
    changes: tuple[Change, ...] = ()
    comment_text: str | None = None
    card_id: str = ""
    root_id: str = ""
    subtasks: tuple[SubtaskChanges, ...] = ()
