import logging
import threading
from datetime import datetime, timedelta, timezone

import httpx

from noty.config import UserConfig
from noty.jotty import JottyClient
from noty.models import Comment
from noty.service import UNSTABLE_LIMIT, FetchResult, Notifier, keep_unstamped_assignees, make_comments_reader, next_snapshot
from noty.state import StateStore

from tests.helpers import T1, api_board, api_checklist, api_item, board, card, edited, kanban_item

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
KEYS = {"ck_a": "alice", "ck_b": "bob"}


class FakeJotty:
    """Serves /api/user, /api/checklists and /api/kanban/{id} from mutable in-memory boards."""

    def __init__(self):
        self.items = [api_item("c1", createdBy="alice", lastModifiedBy="alice")]
        self.assignees: dict[str, str] = {}
        self.owner = "alice"
        self.fail = False
        self.hidden = False
        self.gone: set[str] = set()
        self.broken: set[str] = set()
        self.on_list = None
        self.writes = 0
        self.list_updated_at: str | None = None
        self.detail_updated_at: str | None = None
        self.keys = dict(KEYS)
        self.requests: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request.url.path)
        user = self.keys.get(request.headers.get("x-api-key", ""))
        if user is None:
            return httpx.Response(401, json={"error": "Unauthorized"})
        if self.fail:
            return httpx.Response(500, text="down")
        if request.url.path == "/api/user":
            return httpx.Response(200, json={"user": {"username": user}})
        if request.url.path == "/api/checklists":
            extra = {"updatedAt": self.list_updated_at} if self.list_updated_at else {}
            boards = [] if self.hidden else [api_checklist("b1", items=self.items, owner=self.owner, **extra)]
            response = httpx.Response(200, json={"checklists": boards, "total": len(boards)})
            if self.on_list:
                self.on_list(user)  # a change landing right after the list was read
            return response
        board_id = request.url.path.rsplit("/", 1)[1]
        if board_id in self.gone:
            return httpx.Response(404, json={"error": "Not found"})
        if board_id in self.broken:
            return httpx.Response(500, json={"error": "Internal error"})
        items = [kanban_item(item["id"], self.assignees.get(item["id"])) for item in self.items]
        detail_at = self.detail_updated_at or self.list_updated_at
        extra = {"updatedAt": detail_at} if detail_at else {}
        return httpx.Response(200, json={"board": api_board(board_id, items=items, **extra)})

    def edit(self, by: str, **fields):
        """A write through Jotty: stamps the card and changes the board file's mtime."""
        self.writes += 1
        stamp = f"2026-10-06T10:{self.writes:02d}:00.000Z"
        self.items = [{**self.items[0], **fields, "lastModifiedBy": by, "lastModifiedAt": stamp}]
        self.list_updated_at = stamp


class FakeTelegram:
    def __init__(self, fail_on: int | None = None, on_send=None):
        self.sent: list[tuple[int, str]] = []
        self.fail_on = fail_on
        self.on_send = on_send

    def send(self, chat_id, text):
        if self.on_send:
            self.on_send()
        if self.fail_on is not None and len(self.sent) == self.fail_on:
            self.fail_on = None
            raise RuntimeError("boom")
        self.sent.append((chat_id, text))
        return True


def make_notifier(tmp_path, jotty, comments=None, telegram=None, stop=None, clock=lambda: NOW):
    users = [UserConfig(username="alice", api_key="ck_a", chat_id=1), UserConfig(username="bob", api_key="ck_b", chat_id=2)]
    telegram = telegram or FakeTelegram()
    calls = []

    def reader(owner, uuid):
        calls.append((owner, uuid))
        return (comments or {}).get((owner, uuid), [])

    notifier = Notifier(
        users=users,
        state=StateStore(tmp_path),
        telegram=telegram,
        client_factory=lambda user: JottyClient("http://jotty:3000", user.api_key.get_secret_value(), transport=httpx.MockTransport(jotty.handler)),
        comments_reader=reader,
        app_url="https://jotty.example.com",
        stop=stop,
        clock=clock,
    )
    notifier.reader_calls = calls
    return notifier, telegram


def test_baseline_then_change_by_bob_notifies_alice_only(tmp_path):
    jotty = FakeJotty()
    notifier, telegram = make_notifier(tmp_path, jotty)

    notifier.poll_all()
    assert telegram.sent == []

    jotty.edit("bob", status="in_progress")
    notifier.poll_all()

    [(chat_id, text)] = telegram.sent
    assert chat_id == 1
    assert "Card updated" in text and "status: To Do → In Progress" in text and "by bob" in text
    assert "https://jotty.example.com/checklist/b1" in text

    notifier.poll_all()
    assert len(telegram.sent) == 1


def test_assignment_reaches_assignee(tmp_path):
    jotty = FakeJotty()
    notifier, telegram = make_notifier(tmp_path, jotty)
    notifier.poll_all()

    jotty.assignees = {"c1": "bob"}
    jotty.edit("alice")
    notifier.poll_all()

    [(chat_id, text)] = telegram.sent
    assert chat_id == 2 and "Assigned to you" in text and "by alice" in text


def test_failed_poll_keeps_snapshot_and_catches_up(tmp_path):
    jotty = FakeJotty()
    notifier, telegram = make_notifier(tmp_path, jotty)
    notifier.poll_all()

    jotty.fail = True
    jotty.edit("bob", completed=True)
    notifier.poll_all()
    assert telegram.sent == []

    jotty.fail = False
    notifier.poll_all()
    [(chat_id, text)] = telegram.sent
    assert chat_id == 1 and "Card completed" in text


# --- key ownership (M3) -----------------------------------------------------------------------


def test_key_of_another_user_is_refused(tmp_path):
    jotty = FakeJotty()
    jotty.keys = {"ck_a": "bob", "ck_b": "bob"}
    notifier, _ = make_notifier(tmp_path, jotty)
    notifier.poll_all()
    assert StateStore(tmp_path).load("alice") is None
    assert StateStore(tmp_path).load("bob") is not None


def test_key_is_verified_once_and_again_after_401(tmp_path):
    jotty = FakeJotty()
    notifier, _ = make_notifier(tmp_path, jotty)
    notifier.poll_all()
    notifier.poll_all()
    assert jotty.requests.count("/api/user") == 2  # once per user

    jotty.keys.pop("ck_a")
    notifier.poll_all()
    jotty.keys["ck_a"] = "alice"
    notifier.poll_all()
    # The 401 dropped alice's verification, so her key is checked once more.
    assert jotty.requests.count("/api/user") == 3


# --- missing and returning boards (H1) --------------------------------------------------------


def test_board_gone_mid_poll_is_remembered(tmp_path):
    jotty = FakeJotty()
    notifier, _ = make_notifier(tmp_path, jotty)
    notifier.poll_all()

    jotty.edit("alice")
    jotty.gone = {"b1"}
    notifier.poll_all()
    saved = StateStore(tmp_path).load("alice")["b1"]
    assert saved.missing_since == NOW.isoformat()


def test_board_returning_after_archive_does_not_resend_assignments(tmp_path):
    jotty = FakeJotty()
    jotty.items = [api_item("c1", createdBy="bob", lastModifiedBy="bob")]
    jotty.assignees = {"c1": "alice"}
    notifier, telegram = make_notifier(tmp_path, jotty)
    notifier.poll_all()

    jotty.hidden = True
    notifier.poll_all()
    notifier.poll_all()
    # Announced once to each user with a card there: bob created it, it is assigned to alice.
    assert sorted(chat for chat, _ in telegram.sent) == [1, 2]
    assert all("Board unavailable" in text for _, text in telegram.sent)

    jotty.hidden = False
    notifier.poll_all()
    assert len(telegram.sent) == 2
    assert StateStore(tmp_path).load("alice")["b1"].missing_since is None


def test_comments_posted_while_board_was_hidden_are_sent(tmp_path):
    jotty = FakeJotty()
    comments = {("alice", "b1"): []}
    notifier, telegram = make_notifier(tmp_path, jotty, comments)
    notifier.poll_all()

    jotty.hidden = True
    notifier.poll_all()
    comments[("alice", "b1")] = [Comment("m1", "c1", "bob", "While away", "2026-10-06T10:00:00Z")]
    jotty.hidden = False
    notifier.poll_all()

    assert [text.split("\n")[0] for _, text in telegram.sent] == [
        "#jotty \U0001f5c4\ufe0f <b>Board unavailable</b>",
        "#jotty \U0001f4ac <b>New comment</b>",
    ]
    assert "While away" in telegram.sent[1][1]


def test_next_snapshot_retention():
    retention = timedelta(days=30)
    old = board(uuid="b1")
    snap = next_snapshot({"b1": old}, FetchResult(), NOW, retention)
    assert snap["b1"].missing_since == NOW.isoformat()

    later = NOW + timedelta(days=29)
    assert "b1" in next_snapshot(snap, FetchResult(), later, retention)
    expired = NOW + timedelta(days=31)
    assert next_snapshot(snap, FetchResult(), expired, retention) == {}


def test_next_snapshot_keeps_unstable_boards_as_they_were():
    old = board(uuid="b1")
    snap = next_snapshot({"b1": old}, FetchResult(unstable={"b1": board(uuid="b1")}), NOW, timedelta(days=30))
    assert snap["b1"] is old and old.missing_since is None


# --- list/detail drift (M2) -------------------------------------------------------------------


def test_board_changed_between_calls_is_retried_next_poll(tmp_path):
    jotty = FakeJotty()
    notifier, telegram = make_notifier(tmp_path, jotty)
    notifier.poll_all()

    jotty.edit("bob", status="in_progress")
    jotty.detail_updated_at = "2026-10-06T09:59:59.000Z"
    notifier.poll_all()
    assert telegram.sent == []
    assert StateStore(tmp_path).load("alice")["b1"].cards["c1"].status == "todo"

    jotty.detail_updated_at = None
    notifier.poll_all()
    assert len(telegram.sent) == 1


def test_board_that_never_reads_consistently_is_accepted_after_a_few_polls(tmp_path):
    jotty = FakeJotty()
    notifier, telegram = make_notifier(tmp_path, jotty)
    notifier.poll_all()

    jotty.edit("bob", status="in_progress")
    jotty.detail_updated_at = "2026-10-06T09:59:59.000Z"
    for _ in range(UNSTABLE_LIMIT - 1):
        notifier.poll_all()
    assert telegram.sent == []
    notifier.poll_all()
    assert len(telegram.sent) == 1


def test_baseline_includes_inconsistent_boards(tmp_path):
    jotty = FakeJotty()
    jotty.detail_updated_at = "2026-10-06T09:59:59.000Z"
    notifier, telegram = make_notifier(tmp_path, jotty)
    notifier.poll_all()
    assert "b1" in StateStore(tmp_path).load("alice")
    assert telegram.sent == []


def test_accepted_inconsistent_board_keeps_unstamped_assignee_changes_for_later(tmp_path):
    jotty = FakeJotty()
    jotty.items = [api_item("c1", createdBy="bob", lastModifiedBy="alice")]
    notifier, telegram = make_notifier(tmp_path, jotty)
    notifier.poll_all()

    # Bob assigns the card to alice between the list and detail calls of every poll:
    # the detail call already shows the assignee, the list call not yet the new stamp.
    jotty.assignees = {"c1": "alice"}
    jotty.list_updated_at = "2026-10-06T09:30:00.000Z"
    jotty.detail_updated_at = "2026-10-06T09:30:05.000Z"
    for _ in range(UNSTABLE_LIMIT):
        notifier.poll_all()
    assert telegram.sent == []
    assert StateStore(tmp_path).load("alice")["b1"].cards["c1"].assignee is None

    jotty.detail_updated_at = None
    jotty.edit("bob")
    notifier.poll_all()
    [(chat_id, text)] = telegram.sent
    assert chat_id == 1 and "Assigned to you" in text and "by bob" in text


def test_keep_unstamped_assignees():
    old = board(card("c1"), card("c2"))
    new = board(card("c1", assignee="bob"), edited(card("c2"), "carol", assignee="bob"), card("c3", assignee="bob"))
    kept = keep_unstamped_assignees(new, old)
    assert [kept.cards[c].assignee for c in ("c1", "c2", "c3")] == [None, "bob", "bob"]
    assert keep_unstamped_assignees(new, None) is new


def test_board_that_never_reads_consistently_warns_once(tmp_path, caplog):
    jotty = FakeJotty()
    notifier, _ = make_notifier(tmp_path, jotty)
    notifier.poll_all()

    jotty.list_updated_at = "2026-10-06T09:30:00.000Z"
    with caplog.at_level(logging.WARNING, logger="noty.service"):
        for index in range(UNSTABLE_LIMIT * 3):
            jotty.detail_updated_at = f"2026-10-06T09:31:{index:02d}.000Z"
            notifier.poll_all()
    warnings = [record for record in caplog.records if "keeps changing" in record.getMessage()]
    assert len(warnings) == 2  # once per user


def test_deleted_card_reaches_its_creator_once(tmp_path):
    jotty = FakeJotty()
    notifier, telegram = make_notifier(tmp_path, jotty)
    notifier.poll_all()

    jotty.items = []
    jotty.list_updated_at = "2026-10-06T10:30:00.000Z"
    notifier.poll_all()
    notifier.poll_all()
    [(chat_id, text)] = telegram.sent
    assert chat_id == 1 and "Card deleted" in text


# --- one broken board -------------------------------------------------------------------------


def test_broken_board_keeps_its_state_without_failing_the_user(tmp_path):
    jotty = FakeJotty()
    notifier, telegram = make_notifier(tmp_path, jotty)
    notifier.poll_all()
    (tmp_path / ".heartbeat").unlink()

    jotty.broken = {"b1"}
    jotty.edit("bob", status="in_progress")
    notifier.poll_all()
    saved = StateStore(tmp_path).load("alice")["b1"]
    assert saved.missing_since is None and saved.cards["c1"].status == "todo"
    assert (tmp_path / ".heartbeat").exists()
    assert telegram.sent == []

    jotty.broken = set()
    notifier.poll_all()
    [(chat_id, _)] = telegram.sent
    assert chat_id == 1


# --- unchanged boards skip the detail call ----------------------------------------------------


def test_unchanged_board_file_skips_the_detail_call(tmp_path):
    jotty = FakeJotty()
    jotty.assignees = {"c1": "bob"}
    notifier, telegram = make_notifier(tmp_path, jotty)
    notifier.poll_all()
    detail_calls = jotty.requests.count("/api/kanban/b1")
    assert detail_calls == 2  # baseline for alice and bob

    notifier.poll_all()
    assert jotty.requests.count("/api/kanban/b1") == detail_calls
    assert StateStore(tmp_path).load("alice")["b1"].cards["c1"].assignee == "bob"

    jotty.edit("carol", status="in_progress")
    notifier.poll_all()
    assert jotty.requests.count("/api/kanban/b1") == detail_calls + 2
    assert sorted(chat for chat, _ in telegram.sent) == [1, 2]


def test_unchanged_board_reused_keeps_comments_working(tmp_path):
    jotty = FakeJotty()
    comments = {("alice", "b1"): []}
    notifier, telegram = make_notifier(tmp_path, jotty, comments)
    notifier.poll_all()

    comments[("alice", "b1")] = [Comment("m1", "c1", "bob", "hi", "2026-10-06T10:00:00Z")]
    notifier.poll_all()
    [(chat_id, text)] = telegram.sent
    assert chat_id == 1 and "bob: hi" in text


# --- delivery (M1, L5) ------------------------------------------------------------------------


def test_state_is_saved_before_sending(tmp_path):
    jotty = FakeJotty()
    store = StateStore(tmp_path)
    notifier, _ = make_notifier(tmp_path, jotty, telegram=FakeTelegram(on_send=lambda: snapshots.append(store.load("alice"))))
    snapshots = []
    notifier.poll_all()

    jotty.edit("bob", status="in_progress")
    notifier.poll_all()
    assert snapshots[0]["b1"].cards["c1"].status == "in_progress"


def test_save_failure_sends_nothing_and_retries(tmp_path, monkeypatch):
    jotty = FakeJotty()
    notifier, telegram = make_notifier(tmp_path, jotty)
    notifier.poll_all()

    jotty.edit("bob", status="in_progress")
    original = StateStore.save
    monkeypatch.setattr(StateStore, "save", lambda self, username, boards: (_ for _ in ()).throw(OSError("disk full")))
    notifier.poll_all()
    assert telegram.sent == []

    monkeypatch.setattr(StateStore, "save", original)
    notifier.poll_all()
    assert len(telegram.sent) == 1


def test_send_exception_does_not_stop_other_messages_or_repeat(tmp_path):
    jotty = FakeJotty()
    comments = {("alice", "b1"): []}
    notifier, telegram = make_notifier(tmp_path, jotty, comments, telegram=FakeTelegram(fail_on=0))
    notifier.poll_all()

    jotty.edit("bob", status="in_progress")
    comments[("alice", "b1")] = [Comment("m1", "c1", "bob", "hi", "2026-10-06T10:00:00Z")]
    notifier.poll_all()
    assert len(telegram.sent) == 1 and "New comment" in telegram.sent[0][1]

    notifier.poll_all()
    assert len(telegram.sent) == 1


def test_stop_skips_remaining_messages(tmp_path):
    jotty = FakeJotty()
    comments = {("alice", "b1"): []}
    stop = threading.Event()
    notifier, telegram = make_notifier(tmp_path, jotty, comments, telegram=FakeTelegram(on_send=stop.set), stop=stop)
    notifier.poll_all()

    jotty.edit("bob", status="in_progress")
    comments[("alice", "b1")] = [Comment("m1", "c1", "bob", "hi", "2026-10-06T10:00:00Z")]
    notifier.poll_all()
    assert len(telegram.sent) == 1


# --- comments ---------------------------------------------------------------------------------


def test_comments_on_shared_board_are_read_from_its_owner(tmp_path):
    jotty = FakeJotty()
    jotty.owner = "carol"
    jotty.items = [api_item("c1", createdBy="alice", lastModifiedBy="alice")]
    comments = {("carol", "b1"): []}
    notifier, telegram = make_notifier(tmp_path, jotty, comments)
    notifier.poll_all()

    comments[("carol", "b1")] = [Comment("m1", "c1", "bob", "Looks good", "2026-10-06T10:00:00Z")]
    notifier.poll_all()

    assert ("carol", "b1") in notifier.reader_calls
    [(chat_id, text)] = telegram.sent
    assert chat_id == 1 and "bob: Looks good" in text


def test_comment_on_a_card_created_during_the_poll_is_not_lost(tmp_path):
    jotty = FakeJotty()
    comments = {("alice", "b1"): []}
    notifier, telegram = make_notifier(tmp_path, jotty, comments)
    notifier.poll_all()

    def bob_acts(user):
        # Right after alice's board list was read: bob adds a card for her and comments on it.
        if user != "alice" or jotty.on_list is None:
            return
        jotty.on_list = None
        jotty.items = [*jotty.items, api_item("c2", "Review", createdBy="bob", lastModifiedBy="bob", lastModifiedAt=T1)]
        jotty.assignees = {"c2": "alice"}
        jotty.list_updated_at = T1
        comments[("alice", "b1")] = [Comment("m1", "c2", "bob", "Yours now", "2026-10-06T10:00:00Z")]

    jotty.on_list = bob_acts
    notifier.poll_all()
    assert telegram.sent == []

    notifier.poll_all()
    alice = [text for chat, text in telegram.sent if chat == 1]
    assert len(alice) == 2
    assert "Assigned to you" in alice[0] and "bob: Yours now" in alice[1]


def test_comments_reader_requires_checklists_directory(tmp_path):
    assert make_comments_reader(None)("alice", "b1") is None
    assert make_comments_reader(tmp_path / "missing")("alice", "b1") is None
    assert make_comments_reader(tmp_path)("alice", "b1") is None
    (tmp_path / "checklists" / "alice").mkdir(parents=True)
    assert make_comments_reader(tmp_path)("alice", "b1") == []


def test_run_stops_and_writes_heartbeat(tmp_path):
    jotty = FakeJotty()
    stop = threading.Event()
    notifier, _ = make_notifier(tmp_path, jotty, stop=stop)
    stop.set()
    notifier.run(1)
    assert not (tmp_path / ".heartbeat").exists()

    stop.clear()
    timer = threading.Timer(0.2, stop.set)
    timer.start()
    notifier.run(1)
    assert (tmp_path / ".heartbeat").exists()


def test_no_heartbeat_when_every_user_fails(tmp_path):
    jotty = FakeJotty()
    jotty.fail = True
    notifier, _ = make_notifier(tmp_path, jotty)
    notifier.poll_all()
    assert not (tmp_path / ".heartbeat").exists()


def test_unexpected_errors_do_not_stop_other_users(tmp_path, monkeypatch):
    jotty = FakeJotty()
    notifier, _ = make_notifier(tmp_path, jotty)
    calls = []
    original = notifier.poll_user

    def flaky(user):
        calls.append(user.username)
        if user.username == "alice":
            raise RuntimeError("unexpected")
        original(user)

    monkeypatch.setattr(notifier, "poll_user", flaky)
    notifier.poll_all()
    assert calls == ["alice", "bob"]
    assert StateStore(tmp_path).load("bob") is not None
