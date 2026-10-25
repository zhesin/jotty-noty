"""Poll loop: fetch each user's boards, diff against the saved snapshot, send events."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from noty.comments import read_comments
from noty.config import Settings, UserConfig
from noty.diff import compute_events, next_comment_ids
from noty.jotty import BoardGone, JottyClient, JottyError, Unauthorized, build_board_state, same_version
from noty.models import BoardState, Comment
from noty.render import render
from noty.state import StateStore
from noty.telegram import TelegramClient

log = logging.getLogger(__name__)

ClientFactory = Callable[[UserConfig], JottyClient]
CommentsReader = Callable[[str | None, str], list[Comment] | None]
Clock = Callable[[], datetime]


# A board whose list and detail reads keep disagreeing is accepted after this many polls in a row.
# (Jotty rewrites a due recurring card of a shared board on every read, so this can last.)
UNSTABLE_LIMIT = 3


@dataclass
class FetchResult:
    boards: dict[str, BoardState] = field(default_factory=dict)
    # Boards that changed between the list and detail calls, as read anyway.
    unstable: dict[str, BoardState] = field(default_factory=dict)
    # Boards whose detail call failed; their previous state is kept.
    failed: set[str] = field(default_factory=set)
    # Boards taken from the previous snapshot because their file did not change.
    reused: int = 0


def fetch_boards(client: JottyClient, previous: dict[str, BoardState] | None = None) -> FetchResult:
    """All boards visible to the client's user. Raises JottyError if the board list cannot be read.

    A board whose updatedAt (its file's mtime) equals the one in `previous` has not changed,
    so its detail call is skipped and its previous state is reused. A board whose detail call
    fails is left out and listed in `failed`, so one broken board does not block the others.
    """
    result = FetchResult()
    for checklist in client.list_boards():
        uuid = checklist["id"]
        old = (previous or {}).get(uuid)
        if old is not None and old.updated_at is not None and old.updated_at == checklist.get("updatedAt"):
            result.boards[uuid] = replace(old, missing_since=None)
            result.reused += 1
            continue
        try:
            kanban_board = client.get_board(uuid)
        except BoardGone:
            log.info("Board %s disappeared during the poll, treating it as missing", uuid)
            continue
        except Unauthorized:
            raise
        except JottyError as exc:
            log.warning("Cannot read board %s, keeping its previous state: %s", uuid, exc)
            result.failed.add(uuid)
            continue
        board = build_board_state(checklist, kanban_board)
        if same_version(checklist, kanban_board):
            result.boards[uuid] = board
        else:
            result.unstable[uuid] = board
    return result


def gone_boards(previous: dict[str, BoardState] | None, fetched: FetchResult) -> set[str]:
    """Boards that were visible last time and are missing now (not just unreadable or inconsistent)."""
    return {
        uuid
        for uuid, old in (previous or {}).items()
        if old.missing_since is None
        and uuid not in fetched.boards
        and uuid not in fetched.unstable
        and uuid not in fetched.failed
    }


def next_snapshot(
    previous: dict[str, BoardState] | None,
    fetched: FetchResult,
    now: datetime,
    retention: timedelta,
) -> dict[str, BoardState]:
    """Boards to remember: everything seen now, plus missing boards until the retention runs out.

    Remembering a missing board means that when it comes back (unarchived, shared again, or after
    a read error) it is compared with its old state instead of looking brand new.
    """
    snapshot = dict(fetched.boards)
    for uuid, old in (previous or {}).items():
        if uuid in snapshot:
            continue
        if uuid in fetched.unstable or uuid in fetched.failed:
            snapshot[uuid] = old
            continue
        since = old.missing_since or now.isoformat()
        if now - datetime.fromisoformat(since) <= retention:
            snapshot[uuid] = replace(old, missing_since=since)
    return snapshot


def keep_unstamped_assignees(board: BoardState, old: BoardState | None) -> BoardState:
    """Undo assignee changes that came without a new stamp on a board read inconsistently.

    Card fields come from the list call and assignees from the later detail call. An assignee that
    changed while the card's lastModifiedAt did not belongs to a write the list call missed; keeping
    the old one lets the next read report it with the right stamp and author.
    """
    if old is None:
        return board
    cards = {}
    for card_id, card in board.cards.items():
        previous = old.cards.get(card_id)
        if previous is not None and previous.last_modified_at == card.last_modified_at and previous.assignee != card.assignee:
            card = replace(card, assignee=previous.assignee)
        cards[card_id] = card
    return replace(board, cards=cards)


def make_comments_reader(data_dir: Path | None) -> CommentsReader:
    if data_dir is None:
        log.warning("JOTTY_DATA_DIR is not set: comment notifications are disabled")
        return lambda owner, uuid: None
    if not (data_dir / "checklists").is_dir():
        log.warning("%s/checklists is not a directory: comment notifications are disabled", data_dir)
        return lambda owner, uuid: None
    return lambda owner, uuid: read_comments(data_dir, owner, uuid)


class Notifier:
    def __init__(
        self,
        users: list[UserConfig],
        state: StateStore,
        telegram: TelegramClient,
        client_factory: ClientFactory,
        comments_reader: CommentsReader,
        app_url: str | None = None,
        retention: timedelta = timedelta(days=30),
        stop: threading.Event | None = None,
        clock: Clock = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._users = users
        self._state = state
        self._telegram = telegram
        self._client_factory = client_factory
        self._comments_reader = comments_reader
        self._app_url = app_url
        self._retention = retention
        self._stop = stop or threading.Event()
        self._clock = clock
        # Users whose API key was confirmed to belong to them.
        self._verified: set[str] = set()
        # Consecutive inconsistent reads per (username, board uuid).
        self._unstable_polls: dict[tuple[str, str], int] = {}
        # Boards already warned about for never reading consistently, until they do.
        self._unstable_warned: set[tuple[str, str]] = set()

    def _fetch(self, user: UserConfig, previous: dict[str, BoardState] | None) -> FetchResult | None:
        try:
            with self._client_factory(user) as client:
                if user.username not in self._verified:
                    owner = client.current_username()
                    if owner != user.username:
                        log.error("The API key configured for %s belongs to %s; skipping this user", user.username, owner)
                        return None
                    self._verified.add(user.username)
                return fetch_boards(client, previous)
        except Unauthorized as exc:
            self._verified.discard(user.username)
            log.error("API key for %s was rejected (%s); was it regenerated?", user.username, exc)
        except JottyError as exc:
            log.error("Poll failed for %s: %s", user.username, exc)
        # Leave the old snapshot untouched so nothing is lost or re-sent on the next poll.
        return None

    def _settle_unstable(self, username: str, fetched: FetchResult, previous: dict[str, BoardState] | None) -> None:
        """Retry inconsistent boards next poll, but accept them after UNSTABLE_LIMIT polls or on the baseline."""
        for uuid in fetched.boards:
            self._unstable_polls.pop((username, uuid), None)
            self._unstable_warned.discard((username, uuid))
        for uuid, board in list(fetched.unstable.items()):
            key = (username, uuid)
            count = self._unstable_polls.get(key, 0) + 1
            if previous is None or count >= UNSTABLE_LIMIT:
                if previous is not None:
                    if key in self._unstable_warned:
                        log.debug("Board %s still changes between reads; accepting it as read", uuid)
                    else:
                        log.warning("Board %s keeps changing between reads; accepting it as read", uuid)
                        self._unstable_warned.add(key)
                    board = keep_unstamped_assignees(board, previous.get(uuid))
                fetched.boards[uuid] = board
                del fetched.unstable[uuid]
                self._unstable_polls.pop(key, None)
            else:
                log.info("Board %s changed during the poll, retrying it next time", uuid)
                self._unstable_polls[key] = count

    def poll_user(self, user: UserConfig) -> None:
        previous = self._state.load(user.username)
        # Comments of known boards are read before the boards themselves. A comment seen now is then
        # compared with a board state at least as new, so its card, and whom it is assigned to, is known;
        # otherwise a comment on a card created or reassigned mid-poll would be marked seen and never sent.
        early_comments = {uuid: self._comments_reader(board.owner, uuid) for uuid, board in (previous or {}).items()}
        fetched = self._fetch(user, previous)
        if fetched is None:
            return
        self._settle_unstable(user.username, fetched, previous)

        comments: dict[str, list[Comment] | None] = {}
        for uuid, board in fetched.boards.items():
            old = (previous or {}).get(uuid)
            if old is not None and old.owner == board.owner:
                comments[uuid] = early_comments[uuid]
            else:
                # A board new to this user (or with a new owner): its comments only form a baseline, so reading them now is fine.
                comments[uuid] = self._comments_reader(board.owner, uuid)
        for uuid, board in fetched.boards.items():
            board.comment_ids = next_comment_ids(previous.get(uuid) if previous else None, comments[uuid])

        events = compute_events(user.username, previous, fetched.boards, comments, gone_boards(previous, fetched))
        if previous is None:
            log.info("Baseline recorded for %s: %d boards", user.username, len(fetched.boards))

        # Save before sending: delivery is at most once, a failure never causes repeats.
        self._state.save(user.username, next_snapshot(previous, fetched, self._clock(), self._retention))

        self._beat()

        sent = 0
        for index, event in enumerate(events):
            if self._stop.is_set():
                log.warning("Stopping: %d notifications for %s were not sent", len(events) - index, user.username)
                break
            try:
                if self._telegram.send(user.chat_id, render(event, self._app_url)):
                    sent += 1
                    self._beat()
            except Exception:
                log.exception("Failed to send a notification to %s", user.username)
        if events:
            log.info("Sent %d of %d notifications to %s", sent, len(events), user.username)
        log.debug("Polled %s: %d boards, %d unchanged", user.username, len(fetched.boards), fetched.reused)

    def _beat(self) -> None:
        """Heartbeat for the health check: written only after real progress (a poll or a delivery)."""
        try:
            self._state.touch_heartbeat()
        except OSError as exc:
            log.warning("Cannot write heartbeat: %s", exc)

    def poll_all(self) -> None:
        for user in self._users:
            if self._stop.is_set():
                return
            try:
                self.poll_user(user)
            except Exception:
                log.exception("Unexpected error while polling %s", user.username)

    def run(self, interval: int) -> None:
        log.info("Polling %d users every %ds", len(self._users), interval)
        while not self._stop.is_set():
            self.poll_all()
            self._stop.wait(interval)


def build_notifier(settings: Settings, users: list[UserConfig], stop: threading.Event) -> Notifier:
    def client_factory(user: UserConfig) -> JottyClient:
        return JottyClient(settings.jotty_url, user.api_key.get_secret_value(), settings.request_timeout)

    return Notifier(
        users=users,
        state=StateStore(settings.state_dir),
        telegram=TelegramClient(
            settings.telegram_bot_token.get_secret_value(),
            settings.request_timeout,
            sleep=stop.wait,
        ),
        client_factory=client_factory,
        comments_reader=make_comments_reader(settings.jotty_data_dir),
        app_url=settings.app_url,
        retention=timedelta(days=settings.board_retention_days),
        stop=stop,
    )
