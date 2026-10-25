"""Turn two snapshots of a user's boards into the events that user should hear about.

A user U is notified only when someone else (actor != U) touched a card that is theirs:
the card was created by U, or is assigned to U (before or after the change).
A comment that mentions @U reaches U whoever's card it is on.
Board ownership grants nothing extra. Edited comments are ignored. Deletions and vanished boards
record no author, so they go to everyone concerned, including whoever did it.
"""

from __future__ import annotations

import re
from collections import defaultdict

from noty.models import BoardState, CardState, Change, Comment, Event, EventKind, SubtaskChanges

# Compared card fields in display order: (CardState attribute, label shown in the message).
TRACKED_FIELDS: tuple[tuple[str, str], ...] = (
    ("text", "title"),
    ("status", "status"),
    ("completed", "completed"),
    ("assignee", "assignee"),
    ("priority", "priority"),
    ("target_date", "target date"),
    ("description", "description"),
)

# Jotty gives items without a stored id a positional one, `${fileId}-${level}-${counter}`
# (source: app/_utils/checklist-utils.ts generateItemId). Such ids shift when lines are inserted
# into a board file edited outside Jotty. Ids Jotty creates itself contain a 13-digit Date.now().
_POSITIONAL_SUFFIX = re.compile(r"-\d+-\d+$")
_TIMESTAMP = re.compile(r"\d{13}")


def is_positional_id(card_id: str) -> bool:
    return bool(_POSITIONAL_SUFFIX.search(card_id)) and not _TIMESTAMP.search(card_id)

# Only these kinds are folded into their top-level card's message.
_GROUPABLE = (EventKind.UPDATED, EventKind.COMPLETED)


def _display(value: object) -> str | None:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if value is None or value == "":
        return None
    return str(value)


def _field_changes(old: CardState, new: CardState, old_board: BoardState, new_board: BoardState) -> list[Change]:
    changes: list[Change] = []
    for attr, label in TRACKED_FIELDS:
        before, after = getattr(old, attr), getattr(new, attr)
        if _display(before) == _display(after):
            continue
        if attr == "status":
            before = old_board.statuses.get(before, before) if before else before
            after = new_board.statuses.get(after, after) if after else after
        if attr == "description":
            # Descriptions can be long; only say that it changed (old and new both None).
            changes.append(Change(label, None, None))
            continue
        changes.append(Change(label, _display(before), _display(after)))
    return changes


def _card_path(card: CardState) -> tuple[str, ...]:
    return (*card.parents, card.text)


def _is_theirs(username: str, *cards: CardState) -> bool:
    return any(card.created_by == username or card.assignee == username for card in cards)


def _content(card: CardState) -> tuple[tuple[str, ...], str]:
    return (card.parents, card.text)


class _OldCards:
    """Finds a card's previous state, coping with Jotty's positional ids."""

    def __init__(self, board: BoardState, new_board: BoardState) -> None:
        self.board = board
        self._by_content: dict[tuple[tuple[str, ...], str], list[CardState]] = defaultdict(list)
        for card in board.cards.values():
            self._by_content[_content(card)].append(card)
        self._new_contents = {_content(card) for card in new_board.cards.values()}

    def match(self, card: CardState) -> CardState | None:
        same_id = self.board.cards.get(card.id)
        if not is_positional_id(card.id):
            return same_id
        key = _content(card)
        if same_id is not None and _content(same_id) == key:
            return same_id
        # The id may now point at another line; a unique card with the same content is the real match.
        candidates = self._by_content.get(key, [])
        if len(candidates) == 1:
            return candidates[0]
        if same_id is not None and _content(same_id) in self._new_contents:
            # The card that had this id moved to another line, so this one is new.
            return None
        return same_id


def _descendants(board: BoardState, card_id: str) -> list[CardState]:
    children: dict[str, list[CardState]] = defaultdict(list)
    for card in board.cards.values():
        if card.parent_id:
            children[card.parent_id].append(card)
    found: list[CardState] = []
    stack = list(children.get(card_id, []))
    while stack:
        child = stack.pop()
        found.append(child)
        stack.extend(children.get(child.id, []))
    return found


def _ancestors(board: BoardState, card: CardState) -> list[CardState]:
    found: list[CardState] = []
    seen = {card.id}
    while card.parent_id and card.parent_id in board.cards and card.parent_id not in seen:
        card = board.cards[card.parent_id]
        seen.add(card.id)
        found.append(card)
    return found


def _latest_restamped(cards: list[CardState], old_cards: _OldCards) -> CardState | None:
    """The most recently stamped card among those Jotty stamped since the previous poll."""
    restamped = [
        card
        for card in cards
        if card.last_modified_at
        and ((previous := old_cards.match(card)) is None or previous.last_modified_at != card.last_modified_at)
    ]
    return max(restamped, key=lambda card: card.last_modified_at or "") if restamped else None


def _actor(card: CardState, old: CardState, board: BoardState, old_cards: _OldCards) -> str | None:
    """Who made this change.

    Jotty stamps lastModifiedBy/At on the card a person edits, but not on cards it changes as a
    side effect: a parent completed by its last open sub-card, children completed with their parent,
    or a recurring card reset. Those changes are attributed to the related card that was stamped.
    """
    if card.last_modified_at != old.last_modified_at:
        return card.last_modified_by
    for related in (_descendants(board, card.id), _ancestors(board, card)):
        stamped = _latest_restamped(related, old_cards)
        if stamped is not None:
            return stamped.last_modified_by
    # Nothing was stamped (e.g. a recurring reset): fall back to the card's last known editor.
    return card.last_modified_by


def _event(kind: EventKind, board: BoardState, card: CardState, actor: str | None, changes: list[Change] | tuple = ()) -> Event:
    return Event(
        kind=kind,
        board_uuid=board.uuid,
        board_title=board.title,
        card_path=_card_path(card),
        actor=actor,
        changes=tuple(changes),
        card_id=card.id,
        root_id=board.root_of(card).id,
    )


def _card_events(username: str, old_board: BoardState | None, new_board: BoardState) -> list[Event]:
    old_cards = _OldCards(old_board, new_board) if old_board else None
    events: list[Event] = []
    for card in new_board.cards.values():
        old = old_cards.match(card) if old_cards else None

        if old_board is None or old_cards is None or old is None:
            # New card, or a board seen for the first time: what changed is unknown.
            actor = card.last_modified_by or card.created_by
            if actor == username:
                continue
            if old_board is None and card.completed:
                # A board seen for the first time (shared, copied, back after retention): finished work is history.
                continue
            if card.assignee == username and card.created_by != username:
                events.append(_event(EventKind.ASSIGNED, new_board, card, actor))
            elif card.created_by == username and old_board is not None:
                # Created by U on a known board and already edited by someone else before this poll.
                # (Jotty assigns a card created on a shared board to its creator, so this is not an assignment.)
                events.append(_event(EventKind.UPDATED, new_board, card, actor))
            continue

        changes = _field_changes(old, card, old_board, new_board)
        if not changes or not _is_theirs(username, old, card):
            continue
        actor = _actor(card, old, new_board, old_cards)
        if actor == username:
            continue

        if card.assignee == username and old.assignee != username:
            kind = EventKind.ASSIGNED
        elif card.completed and not old.completed:
            kind = EventKind.COMPLETED
        else:
            kind = EventKind.UPDATED
        events.append(_event(kind, new_board, card, actor, changes))
    return events


def _deleted_cards(old_board: BoardState, new_board: BoardState) -> dict[str, CardState]:
    """Cards of the previous poll that are gone now, by id."""
    old_cards = _OldCards(old_board, new_board)
    matched = {id(old) for card in new_board.cards.values() if (old := old_cards.match(card)) is not None}
    new_contents = {_content(card) for card in new_board.cards.values()}
    return {
        card.id: card
        for card in old_board.cards.values()
        if id(card) not in matched
        # A positional id may just have shifted to another line; only a vanished text counts.
        and not (is_positional_id(card.id) and _content(card) in new_contents)
    }


def _deleted_events(username: str, old_board: BoardState, new_board: BoardState) -> list[Event]:
    """One event per topmost deleted card of U's, listing U's deleted cards beneath it."""
    deleted = _deleted_cards(old_board, new_board)
    groups: dict[str, list[CardState]] = defaultdict(list)
    for card in deleted.values():
        if not _is_theirs(username, card):
            continue
        anchor = card
        for ancestor in _ancestors(old_board, card):
            if ancestor.id not in deleted:
                break
            if _is_theirs(username, ancestor):
                anchor = ancestor
        groups[anchor.id].append(card)

    events: list[Event] = []
    for anchor_id, members in groups.items():
        anchor = deleted[anchor_id]
        path = _card_path(anchor)
        events.append(
            Event(
                EventKind.DELETED,
                new_board.uuid,
                new_board.title,
                path,
                None,
                card_id=anchor.id,
                root_id=old_board.root_of(anchor).id,
                subtasks=tuple(
                    SubtaskChanges(path=_card_path(member)[len(path):])
                    for member in members
                    if member is not anchor
                ),
            )
        )
    return events


def mentions(text: str, username: str) -> bool:
    """Whether `text` mentions @username.

    Jotty's mention picker inserts "@" + the exact username + " " (MentionTextarea.tsx), and Jotty
    compares mentioned names case-sensitively. Its own server-side regex stops at characters such as
    a second "@", so it misses SSO usernames like "name@domain"; the exact name is looked for here
    instead. The mention must start the text or follow whitespace, and must not run on into a longer
    name ("@bob" is not in "@bobby" or "@bob.smith", but is in "@bob, see").
    """
    pattern = rf"(?<!\S)@{re.escape(username)}(?![\w@-]|\.\w)"
    return re.search(pattern, text) is not None


def _comment_events(
    username: str,
    old_board: BoardState | None,
    new_board: BoardState,
    comments: list[Comment] | None,
) -> list[Event]:
    # Without a previous list of comment ids every comment would look new, so stay silent.
    if old_board is None or old_board.comment_ids is None or comments is None:
        return []

    events: list[Event] = []
    for comment in comments:
        if comment.id in old_board.comment_ids or comment.author == username:
            continue
        card = new_board.cards.get(comment.card_id)
        if card is None:
            continue
        mentioned = mentions(comment.text, username)
        if not mentioned and not _is_theirs(username, card):
            continue
        events.append(
            Event(
                EventKind.MENTION if mentioned else EventKind.COMMENT,
                new_board.uuid,
                new_board.title,
                _card_path(card),
                comment.author,
                comment_text=comment.text,
                card_id=card.id,
                root_id=new_board.root_of(card).id,
            )
        )
    return events


def _board_gone_events(username: str, board: BoardState) -> list[Event]:
    """A board that vanished from the list. The API cannot tell archived, deleted and unshared boards
    apart, nor who did it, so it goes to everyone with a card of theirs on it, whoever that was."""
    if not any(_is_theirs(username, card) for card in board.cards.values()):
        return []
    return [Event(EventKind.BOARD_GONE, board.uuid, board.title, (), None)]


def group_subtasks(events: list[Event]) -> list[Event]:
    """Fold updates of cards under the same top-level card, by the same actor, into one message.

    Moving a parent into an auto-complete column stamps every sub-card, which would otherwise
    send one message per sub-card. Assignments and comments always stay separate.
    """
    groups: dict[tuple[str, str, str | None], list[Event]] = defaultdict(list)
    for event in events:
        if event.kind in _GROUPABLE:
            groups[(event.board_uuid, event.root_id, event.actor)].append(event)

    result: list[Event] = []
    emitted: set[tuple[str, str, str | None]] = set()
    for event in events:
        key = (event.board_uuid, event.root_id, event.actor)
        if event.kind not in _GROUPABLE or len(groups[key]) == 1:
            result.append(event)
            continue
        if key in emitted:
            continue
        emitted.add(key)
        members = groups[key]
        root = next((member for member in members if member.card_id == member.root_id), None)
        if root is not None:
            kind, path, changes = root.kind, root.card_path, root.changes
        else:
            all_done = all(member.kind is EventKind.COMPLETED for member in members)
            kind = EventKind.COMPLETED if all_done else EventKind.UPDATED
            path, changes = members[0].card_path[:1], ()
        subtasks = tuple(
            SubtaskChanges(path=member.card_path[1:], changes=member.changes)
            for member in members
            if member is not root
        )
        result.append(
            Event(
                kind=kind,
                board_uuid=event.board_uuid,
                board_title=event.board_title,
                card_path=path,
                actor=event.actor,
                changes=changes,
                card_id=event.root_id,
                root_id=event.root_id,
                subtasks=subtasks,
            )
        )
    return result


def compute_events(
    username: str,
    previous: dict[str, BoardState] | None,
    current: dict[str, BoardState],
    comments: dict[str, list[Comment] | None],
    gone: set[str] | frozenset[str] = frozenset(),
) -> list[Event]:
    """Events for `username`. `previous` is None on the first run, which only records a baseline.

    `previous` may hold boards that are currently missing; a board that comes back is compared
    with its last known state instead of being treated as new. `gone` are the previous boards
    that disappeared in this poll.
    """
    if previous is None:
        return []

    card_events: list[Event] = []
    deleted_events: list[Event] = []
    comment_events: list[Event] = []
    for uuid, board in current.items():
        old_board = previous.get(uuid)
        card_events.extend(_card_events(username, old_board, board))
        if old_board is not None:
            deleted_events.extend(_deleted_events(username, old_board, board))
        comment_events.extend(_comment_events(username, old_board, board, comments.get(uuid)))
    gone_events = [event for uuid in sorted(gone) if uuid in previous for event in _board_gone_events(username, previous[uuid])]
    return group_subtasks(card_events) + deleted_events + comment_events + gone_events


def next_comment_ids(previous: BoardState | None, comments: list[Comment] | None) -> set[str] | None:
    """Comment ids to remember for a board; keeps the old ones when comments could not be read."""
    if comments is not None:
        return {comment.id for comment in comments}
    return previous.comment_ids if previous else None
