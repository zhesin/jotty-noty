from dataclasses import replace

from noty.diff import compute_events, group_subtasks, is_positional_id, mentions, next_comment_ids
from noty.models import Change, Comment, EventKind, SubtaskChanges

from tests.helpers import T1, T2, board, card, edited


def comment(comment_id: str = "m1", card_id: str = "c1", author: str = "bob", text: str = "Please check") -> Comment:
    return Comment(id=comment_id, card_id=card_id, author=author, text=text, created_at="2026-10-06T10:00:00Z")


def events_for(username, previous, current, comments=None):
    return compute_events(username, previous, current, comments or {})


def bobs_card(card_id="c1", **overrides):
    return card(card_id, created_by="bob", last_modified_by="bob", **overrides)


# --- baseline and simple changes -------------------------------------------------------------


def test_first_run_is_silent_baseline():
    current = {"b1": board(card(assignee="alice", last_modified_by="bob"))}
    assert events_for("alice", None, current) == []


def test_unchanged_board_produces_nothing():
    assert events_for("alice", {"b1": board(card())}, {"b1": board(card())}) == []


def test_assignment_by_someone_else():
    old = bobs_card()
    [event] = events_for("alice", {"b1": board(old)}, {"b1": board(edited(old, "bob", assignee="alice"))})

    assert event.kind is EventKind.ASSIGNED
    assert event.actor == "bob"
    assert event.card_path == ("Fix login bug",)
    assert event.changes == (Change("assignee", None, "alice"),)


def test_own_changes_are_ignored():
    old = card()
    assert events_for("alice", {"b1": board(old)}, {"b1": board(edited(old, "alice", status="in_progress"))}) == []


def test_change_to_my_card_by_someone_else_uses_column_labels():
    old = card()
    new = edited(old, "bob", status="in_progress", priority="high")

    [event] = events_for("alice", {"b1": board(old)}, {"b1": board(new)})

    assert event.kind is EventKind.UPDATED
    assert event.actor == "bob"
    assert event.changes == (Change("status", "To Do", "In Progress"), Change("priority", None, "high"))


def test_editing_someone_elses_card_notifies_its_creator_not_the_editor():
    old = bobs_card()
    current = {"b1": board(edited(old, "alice", text="Fix login"))}

    assert events_for("alice", {"b1": board(old)}, current) == []
    [event] = events_for("bob", {"b1": board(old)}, current)
    assert event.changes == (Change("title", "Fix login bug", "Fix login"),)


def test_completion_gets_its_own_kind():
    old = card()
    [event] = events_for("alice", {"b1": board(old)}, {"b1": board(edited(old, "bob", completed=True, status="completed"))})
    assert event.kind is EventKind.COMPLETED
    assert Change("completed", "no", "yes") in event.changes


def test_assignment_wins_over_completion():
    old = bobs_card()
    [event] = events_for("alice", {"b1": board(old)}, {"b1": board(edited(old, "bob", assignee="alice", completed=True))})
    assert event.kind is EventKind.ASSIGNED


def test_cards_that_are_not_mine_are_ignored_even_on_my_board():
    old = bobs_card(assignee="carol")
    assert events_for("alice", {"b1": board(old)}, {"b1": board(edited(old, "carol", status="in_progress"))}) == []


def test_unassigning_me_notifies_me():
    old = bobs_card(assignee="alice")
    [event] = events_for("alice", {"b1": board(old)}, {"b1": board(edited(old, "bob", assignee="carol"))})
    assert event.kind is EventKind.UPDATED
    assert event.changes == (Change("assignee", "alice", "carol"),)


def test_description_change_hides_the_text():
    old = card(description="old")
    [event] = events_for("alice", {"b1": board(old)}, {"b1": board(edited(old, "bob", description="new"))})
    assert event.changes == (Change("description", None, None),)


def test_empty_and_missing_values_are_equal():
    old = card(description=None)
    assert events_for("alice", {"b1": board(old)}, {"b1": board(edited(old, "bob", description=""))}) == []


# --- deleted cards ----------------------------------------------------------------------------


def test_deleted_card_of_mine_is_reported_without_an_actor():
    [event] = events_for("alice", {"b1": board(card(), bobs_card("c2"))}, {"b1": board()})
    assert event.kind is EventKind.DELETED
    assert event.card_path == ("Fix login bug",) and event.actor is None and event.subtasks == ()


def test_deleted_card_assigned_to_me_is_reported():
    [event] = events_for("alice", {"b1": board(bobs_card(assignee="alice"))}, {"b1": board()})
    assert event.kind is EventKind.DELETED


def test_deleted_card_that_is_not_mine_is_silent():
    assert events_for("alice", {"b1": board(bobs_card())}, {"b1": board()}) == []


def test_deleted_parent_lists_my_deleted_cards_beneath_it():
    parent = card("p", text="Parent")
    other = bobs_card("o", text="Other", parent_id="p", parents=("Parent",))
    deep = card("d", text="Deep", parent_id="o", parents=("Parent", "Other"))
    mine = card("m", text="Mine", parent_id="p", parents=("Parent",))
    [event] = events_for("alice", {"b1": board(parent, other, deep, mine)}, {"b1": board()})
    assert event.card_path == ("Parent",)
    assert sorted(sub.path for sub in event.subtasks) == [("Mine",), ("Other", "Deep")]


def test_my_subcard_under_someone_elses_deleted_parent_gets_its_own_message():
    parent = bobs_card("p", text="Parent")
    mine = card("m", text="Mine", parent_id="p", parents=("Parent",))
    [event] = events_for("alice", {"b1": board(parent, mine)}, {"b1": board()})
    assert event.card_path == ("Parent", "Mine") and event.root_id == "p"


def test_deleted_subcard_under_a_remaining_parent():
    parent = bobs_card("p", text="Parent")
    mine = card("m", text="Mine", parent_id="p", parents=("Parent",))
    [event] = events_for("alice", {"b1": board(parent, mine)}, {"b1": board(parent)})
    assert event.card_path == ("Parent", "Mine")


def test_positional_card_whose_text_vanished_is_deleted():
    a = card("f-0-0", text="A")
    b = card("f-0-1", text="B")
    [event] = events_for("alice", {"b1": board(a, b)}, {"b1": board(card("f-0-0", text="B"))})
    assert event.kind is EventKind.DELETED and event.card_path == ("A",)


def test_deletions_on_a_board_seen_for_the_first_time_are_unknown():
    assert events_for("alice", {}, {"b1": board()}) == []


# --- new cards and boards ---------------------------------------------------------------------


def test_new_card_assigned_to_me_by_someone_else():
    [event] = events_for("alice", {"b1": board()}, {"b1": board(bobs_card(assignee="alice"))})
    assert event.kind is EventKind.ASSIGNED
    assert event.changes == ()


def test_new_card_self_assigned_by_its_creator_is_silent():
    # Jotty assigns cards created on a shared board to their creator.
    current = {"b1": board(bobs_card(assignee="bob"))}
    assert events_for("bob", {"b1": board()}, current) == []
    assert events_for("alice", {"b1": board()}, current) == []


def test_my_new_card_edited_by_someone_else_before_the_poll():
    current = {"b1": board(card(last_modified_by="bob"))}
    [event] = events_for("alice", {"b1": board()}, current)
    assert event.kind is EventKind.UPDATED
    assert event.actor == "bob"
    assert event.changes == ()


def test_my_new_self_assigned_card_edited_by_someone_else_is_an_update():
    # On a shared board Jotty assigns a new card to its creator; that is not an assignment to announce.
    [event] = events_for("alice", {"b1": board()}, {"b1": board(card(assignee="alice", last_modified_by="bob"))})
    assert event.kind is EventKind.UPDATED


def test_my_cards_on_a_board_new_to_me_are_silent():
    # A copied or converted board, or one back after the retention period.
    current = {"b2": board(card("c1", last_modified_by="bob"), card("c2", last_modified_by="carol"), uuid="b2")}
    assert events_for("alice", {}, current) == []


def test_my_new_card_is_silent_for_me():
    assert events_for("alice", {"b1": board()}, {"b1": board(card())}) == []


def test_card_on_newly_visible_board_assigned_to_me():
    current = {"b2": board(bobs_card(assignee="alice"), uuid="b2")}
    [event] = events_for("alice", {}, current)
    assert event.kind is EventKind.ASSIGNED
    assert event.board_uuid == "b2"


def test_completed_cards_on_newly_visible_board_are_silent():
    current = {"b2": board(bobs_card(assignee="alice", completed=True), uuid="b2")}
    assert events_for("alice", {}, current) == []


def test_completed_new_card_on_a_known_board_is_still_announced():
    current = {"b1": board(bobs_card(assignee="alice", completed=True))}
    [event] = events_for("alice", {"b1": board()}, current)
    assert event.kind is EventKind.ASSIGNED


def test_returning_board_is_compared_with_its_old_state():
    # The board was missing for a while (archived or unshared) and its snapshot was kept.
    assigned = bobs_card(assignee="alice")
    previous = {"b1": board(assigned)}
    previous["b1"].missing_since = "2026-10-01T00:00:00+00:00"

    assert events_for("alice", previous, {"b1": board(assigned)}) == []
    [event] = events_for("alice", previous, {"b1": board(edited(assigned, "bob", status="in_progress"))})
    assert event.kind is EventKind.UPDATED


# --- actor resolution when Jotty does not stamp the change ------------------------------------


def test_unstamped_change_falls_back_to_the_last_editor():
    # E.g. a recurring card reset on read: fields change, lastModifiedBy/At stay the same.
    old = card(status="completed", completed=True, last_modified_by="bob")
    new = card(status="todo", completed=False, last_modified_by="bob")

    [event] = events_for("alice", {"b1": board(old)}, {"b1": board(new)})
    assert event.actor == "bob"
    assert Change("status", "Completed", "To Do") in event.changes


def test_unstamped_reset_of_a_card_i_edited_last_is_silent():
    old = card(status="completed", completed=True)
    new = card(status="todo", completed=False)
    assert events_for("alice", {"b1": board(old)}, {"b1": board(new)}) == []


def test_completing_my_parent_does_not_notify_me_about_its_children():
    # editItem stamps the parent but completes the children without a stamp.
    parent = card("p", text="Parent")
    subs = [card(f"s{i}", text=f"Sub {i}", parent_id="p", parents=("Parent",), last_modified_by="bob") for i in range(2)]
    previous = {"b1": board(parent, *subs)}
    current = {"b1": board(edited(parent, "alice", completed=True), *(replace(sub, completed=True) for sub in subs))}

    assert events_for("alice", previous, current) == []
    # Bob created the sub-cards, so he hears about it, attributed to alice.
    bobs_view = {"b1": board(parent, *(replace(sub, created_by="bob") for sub in subs))}
    bobs_now = {"b1": board(edited(parent, "alice", completed=True), *(replace(sub, created_by="bob", completed=True) for sub in subs))}
    [event] = events_for("bob", bobs_view, bobs_now)
    assert event.actor == "alice"


def test_parent_auto_completed_by_someone_elses_last_subtask():
    parent = card("p", text="Parent")
    sub = bobs_card("s", text="Sub", parent_id="p", parents=("Parent",))
    previous = {"b1": board(parent, sub)}
    # Bob checks the last open sub-card; Jotty completes the parent without stamping it.
    current = {"b1": board(card("p", text="Parent", completed=True), edited(sub, "bob", completed=True))}

    [event] = events_for("alice", previous, current)
    assert event.kind is EventKind.COMPLETED
    assert event.actor == "bob"
    assert event.card_path == ("Parent",)


def test_parent_auto_completed_by_my_own_last_subtask_is_silent():
    parent = card("p", text="Parent", last_modified_by="bob")
    sub = card("s", text="Sub", parent_id="p", parents=("Parent",))
    previous = {"b1": board(parent, sub)}
    current = {"b1": board(card("p", text="Parent", completed=True, last_modified_by="bob"), edited(sub, "alice", completed=True))}

    assert events_for("alice", previous, current) == []


def test_latest_stamped_descendant_wins():
    parent = card("p", text="Parent")
    a = bobs_card("a", text="A", parent_id="p", parents=("Parent",))
    b = bobs_card("b", text="B", parent_id="p", parents=("Parent",))
    previous = {"b1": board(parent, a, b)}
    current = {"b1": board(card("p", text="Parent", completed=True), edited(a, "carol", at=T1, completed=True), edited(b, "dave", at=T2, completed=True))}

    [event] = [e for e in events_for("alice", previous, current) if e.card_id == "p"]
    assert event.actor == "dave"


# --- sub-cards and grouping -------------------------------------------------------------------


def test_subtask_is_tracked_with_its_parent_path():
    sub = bobs_card("c2", text="Write tests", parent_id="c1", parents=("Fix login bug",))
    previous = {"b1": board(card(), sub)}
    current = {"b1": board(card(), edited(sub, "bob", assignee="alice"))}

    [event] = events_for("alice", previous, current)
    assert event.card_path == ("Fix login bug", "Write tests")
    assert event.root_id == "c1"


def test_moving_parent_groups_subtask_updates_into_one_message():
    parent = card("p", text="Parent")
    subs = [card(f"s{i}", text=f"Sub {i}", parent_id="p", parents=("Parent",)) for i in range(3)]
    previous = {"b1": board(parent, *subs)}
    current = {
        "b1": board(
            edited(parent, "bob", status="completed", completed=True),
            *(edited(sub, "bob", status="completed", completed=True) for sub in subs),
        )
    }

    [event] = events_for("alice", previous, current)

    assert event.kind is EventKind.COMPLETED
    assert event.card_path == ("Parent",)
    assert event.actor == "bob"
    assert [s.path for s in event.subtasks] == [("Sub 0",), ("Sub 1",), ("Sub 2",)]
    assert Change("status", "To Do", "Completed") in event.subtasks[0].changes


def test_subtasks_of_someone_elses_parent_are_grouped_under_it():
    parent = bobs_card("p", text="Parent")
    subs = [card(f"s{i}", text=f"Sub {i}", parent_id="p", parents=("Parent",)) for i in range(2)]
    previous = {"b1": board(parent, *subs)}
    current = {"b1": board(edited(parent, "bob", status="in_progress"), *(edited(sub, "bob", status="in_progress") for sub in subs))}

    [event] = events_for("alice", previous, current)
    assert event.kind is EventKind.UPDATED
    assert event.card_path == ("Parent",)
    assert event.changes == ()
    assert len(event.subtasks) == 2


def test_assignments_and_different_actors_are_not_grouped():
    parent = card("p", text="Parent")
    s1 = bobs_card("s1", text="S1", parent_id="p", parents=("Parent",))
    s2 = card("s2", text="S2", parent_id="p", parents=("Parent",))
    previous = {"b1": board(parent, s1, s2)}
    current = {"b1": board(edited(parent, "bob", priority="high"), edited(s1, "bob", assignee="alice"), edited(s2, "carol", priority="low"))}

    events = events_for("alice", previous, current)
    assert sorted((e.kind.value, e.card_id) for e in events) == [("assigned", "s1"), ("updated", "p"), ("updated", "s2")]


def test_group_subtasks_keeps_single_events():
    old = card()
    events = events_for("alice", {"b1": board(old)}, {"b1": board(edited(old, "bob", priority="high"))})
    assert group_subtasks(events) == events
    assert events[0].subtasks == ()


def test_subtask_changes_dataclass_defaults():
    assert SubtaskChanges(path=("x",)).changes == ()


# --- positional ids ---------------------------------------------------------------------------


def test_positional_id_pattern():
    assert is_positional_id("my-board-0-3")
    assert is_positional_id("todo-1-12")
    assert is_positional_id("Sprint 12-0-3")
    assert is_positional_id("2026-1-4")
    assert not is_positional_id("6f1c2a7e-4b3d-4c8e-9a61-2f5d7e0b8c14-1728200000000")
    assert not is_positional_id("6f1c2a7e-4b3d-4c8e-9a61-2f5d7e0b8c14-1728200000000-3")
    assert not is_positional_id("6f1c2a7e-4b3d-4c8e-9a61-2f5d7e0b8c14-sub-1728200000000")


def test_shifted_positional_ids_do_not_produce_events():
    # A line inserted at the top of a board file edited outside Jotty shifts every positional id.
    a = bobs_card("f-0-0", text="A", assignee="alice")
    b = bobs_card("f-0-1", text="B")
    previous = {"b1": board(a, b)}
    current = {"b1": board(bobs_card("f-0-0", text="New"), bobs_card("f-0-1", text="A", assignee="alice"), bobs_card("f-0-2", text="B"))}

    assert events_for("alice", previous, current) == []


def test_positional_card_edited_in_place_is_still_matched_by_id():
    old = card("f-0-0", text="A")
    [event] = events_for("alice", {"b1": board(old)}, {"b1": board(edited(old, "bob", text="A2"))})
    assert event.changes == (Change("title", "A", "A2"),)


# --- comments ---------------------------------------------------------------------------------


def test_new_comment_on_my_card():
    [event] = events_for("alice", {"b1": board(card())}, {"b1": board(card())}, {"b1": [comment()]})
    assert event.kind is EventKind.COMMENT
    assert event.actor == "bob"
    assert event.comment_text == "Please check"


def test_new_comment_on_card_assigned_to_me():
    mine = bobs_card(assignee="alice")
    [event] = events_for("alice", {"b1": board(mine)}, {"b1": board(mine)}, {"b1": [comment(author="carol")]})
    assert event.actor == "carol"


def test_seen_own_and_foreign_card_comments_are_ignored():
    other = bobs_card("c2", assignee="carol")
    previous = {"b1": board(card(), other, comment_ids={"m1"})}
    comments = [comment("m1"), comment("m2", author="alice"), comment("m3", card_id="c2"), comment("m4", card_id="gone")]
    assert events_for("alice", previous, {"b1": board(card(), other)}, {"b1": comments}) == []


def test_comments_are_silent_without_a_previous_comment_list():
    current = {"b1": board(card())}
    assert events_for("alice", {"b1": board(card(), comment_ids=None)}, current, {"b1": [comment()]}) == []
    # Unreadable comments right now: also silent.
    assert events_for("alice", {"b1": board(card())}, current, {"b1": None}) == []


def test_comments_added_while_board_was_missing_are_reported():
    previous = {"b1": board(card(), comment_ids={"m1"})}
    previous["b1"].missing_since = "2026-10-01T00:00:00+00:00"
    [event] = events_for("alice", previous, {"b1": board(card())}, {"b1": [comment("m1"), comment("m2")]})
    assert event.kind is EventKind.COMMENT


def test_comment_events_come_after_card_events():
    old = card()
    events = events_for("alice", {"b1": board(old)}, {"b1": board(edited(old, "bob", priority="high"))}, {"b1": [comment()]})
    assert [e.kind for e in events] == [EventKind.UPDATED, EventKind.COMMENT]


def test_next_comment_ids():
    prev = board(comment_ids={"m1"})
    assert next_comment_ids(prev, [comment("m2")]) == {"m2"}
    assert next_comment_ids(prev, None) == {"m1"}
    assert next_comment_ids(None, None) is None


# --- boards that disappear --------------------------------------------------------------------


def test_gone_board_with_my_card_notifies_me_once_per_board():
    old = board(bobs_card("c1", assignee="alice"), card("c2"))
    [event] = compute_events("alice", {"b1": old}, {}, {}, gone={"b1"})
    assert event.kind is EventKind.BOARD_GONE and event.board_title == "Sprint 12" and event.actor is None


def test_gone_board_without_my_cards_is_silent():
    old = board(bobs_card("c1"))
    assert compute_events("alice", {"b1": old}, {}, {}, gone={"b1"}) == []


# --- mentions ---------------------------------------------------------------------------------


def test_mention_pattern():
    assert mentions("@alice please check", "alice")
    assert mentions("ok @alice", "alice")
    assert mentions("@alice, see", "alice") and mentions("ask @alice.", "alice")
    assert not mentions("(@alice)", "alice")  # like Jotty's picker, a mention starts after whitespace
    assert mentions("line\n@alice", "alice")
    assert not mentions("@alicea", "alice")
    assert not mentions("@alice.smith", "alice")
    assert not mentions("@alice@example.com", "alice")
    assert not mentions("mail alice@x", "x")
    assert not mentions("@Alice", "alice")
    assert mentions("@carol@example.com please check", "carol@example.com")
    assert not mentions("@carol", "carol@example.com")


def test_mention_on_someone_elses_card_reaches_me():
    old = board(bobs_card(), comment_ids=set())
    new = board(bobs_card())
    [event] = events_for("alice", {"b1": old}, {"b1": new}, {"b1": [comment(text="@alice please check")]})
    assert event.kind is EventKind.MENTION and event.actor == "bob"


def test_mention_on_my_card_is_one_message():
    old = board(card(), comment_ids=set())
    [event] = events_for("alice", {"b1": old}, {"b1": board(card())}, {"b1": [comment(text="@alice look")]})
    assert event.kind is EventKind.MENTION


def test_mentioning_myself_is_silent():
    old = board(bobs_card(), comment_ids=set())
    assert events_for("alice", {"b1": old}, {"b1": board(bobs_card())}, {"b1": [comment(author="alice", text="@alice note")]}) == []


def test_mention_of_someone_else_on_a_foreign_card_is_silent():
    old = board(bobs_card(), comment_ids=set())
    assert events_for("alice", {"b1": old}, {"b1": board(bobs_card())}, {"b1": [comment(text="@carol look")]}) == []
