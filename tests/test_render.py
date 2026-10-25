from noty.models import Change, Event, EventKind, SubtaskChanges
from noty.render import MAX_MESSAGE_LENGTH, render, telegram_length


def event(kind=EventKind.UPDATED, **overrides) -> Event:
    fields = {
        "kind": kind,
        "board_uuid": "b1",
        "board_title": "Sprint 12",
        "card_path": ("Fix login bug",),
        "actor": "bob",
    }
    fields.update(overrides)
    return Event(**fields)


def test_update_message():
    text = render(
        event(changes=(Change("status", "To Do", "In Progress"), Change("description", None, None), Change("priority", "low", None))),
        app_url="https://jotty.example.com/",
    )
    assert text.splitlines() == [
        "#jotty ✏️ <b>Card updated</b>",
        '<a href="https://jotty.example.com/checklist/b1"><b>Fix login bug</b></a> · Sprint 12',
        "<blockquote>status: To Do → In Progress",
        "description: updated",
        "priority: low → none</blockquote>",
        "by bob",
    ]


def test_one_emoji_per_kind():
    assert render(event(EventKind.ASSIGNED)).startswith("#jotty \U0001f4cc <b>Assigned to you</b>")
    assert render(event(EventKind.COMPLETED)).startswith("#jotty ✅ <b>Card completed</b>")
    assert render(event(EventKind.COMMENT, comment_text="hi")).startswith("#jotty \U0001f4ac <b>New comment</b>")


def test_assignment_without_changes_has_no_quote():
    assert render(event(EventKind.ASSIGNED)).splitlines() == [
        "#jotty \U0001f4cc <b>Assigned to you</b>",
        "<b>Fix login bug</b> · Sprint 12",
        "by bob",
    ]


def test_unknown_actor_has_no_by_line():
    assert "by " not in render(event(actor=None, changes=(Change("status", "Done", "To Do"),)))


def test_comment_keeps_line_breaks_and_has_no_by_line():
    text = render(event(EventKind.COMMENT, card_path=("Parent", "Child"), comment_text="Please   check\n\n\nit  today"))
    lines = text.splitlines()
    assert lines[1] == "<b>Parent → Child</b> · Sprint 12"
    assert lines[2:] == ["<blockquote>bob: Please check", "it today</blockquote>"]


def test_long_comment_is_clipped():
    text = render(event(EventKind.COMMENT, comment_text="\n".join(f"line {i}" for i in range(50))))
    assert "line 14" in text and "line 15" not in text
    assert text.endswith("…</blockquote>")


def test_grouped_subtasks():
    text = render(
        event(
            EventKind.COMPLETED,
            card_path=("Parent",),
            changes=(Change("status", "To Do", "Done"),),
            subtasks=(
                SubtaskChanges(("Sub 1",), (Change("completed", "no", "yes"), Change("status", "To Do", "Done"))),
                SubtaskChanges(("Sub 2", "Deep")),
            ),
        )
    )
    assert text.splitlines()[1:] == [
        "<b>Parent</b> · Sprint 12",
        "<blockquote>status: To Do → Done",
        "• Sub 1: completed: no → yes, status: To Do → Done",
        "• Sub 2 → Deep: updated</blockquote>",
        "by bob",
    ]


def test_user_content_is_escaped():
    text = render(event(board_title="<b>x</b>", card_path=("a & b",), actor="<i>", changes=(Change("title", "<old>", "new"),)))
    assert "<b>a &amp; b</b> · &lt;b&gt;x&lt;/b&gt;" in text
    assert "title: &lt;old&gt; → new" in text
    assert "by &lt;i&gt;" in text


def test_no_link_without_app_url():
    assert "href" not in render(event())


def test_long_values_are_clipped():
    text = render(event(changes=(Change("title", "x" * 500, "y"),)))
    assert "x" * 199 + "…" in text
    assert "x" * 200 not in text


def test_telegram_length_counts_utf16_units():
    assert telegram_length("abc") == 3
    assert telegram_length("\U0001f4cc") == 2


def test_many_changes_are_cut_with_a_note_inside_the_quote():
    changes = tuple(Change(f"f{i}", "\U0001f600" * 100, "b" * 100) for i in range(40))
    text = render(event(changes=changes), app_url="https://jotty.example.com")

    assert telegram_length(text) <= MAX_MESSAGE_LENGTH
    lines = text.splitlines()
    assert lines[0] == "#jotty ✏️ <b>Card updated</b>"
    assert lines[1].startswith('<a href="https://jotty.example.com/checklist/b1">')
    assert "more</blockquote>" in lines[-2]
    assert lines[-1] == "by bob"


def test_deep_path_keeps_header_and_footer():
    text = render(event(card_path=tuple("level" * 40 for _ in range(30))), app_url="https://j")
    lines = text.splitlines()
    assert telegram_length(text) <= MAX_MESSAGE_LENGTH
    assert lines[0] == "#jotty ✏️ <b>Card updated</b>"
    assert lines[1].startswith('<a href="https://j/checklist/b1"><b>level')
    assert lines[-1] == "by bob"


def test_board_gone_message():
    text = render(event(EventKind.BOARD_GONE, card_path=(), actor=None), app_url="https://jotty.example.com")
    assert text.splitlines() == [
        "#jotty \U0001f5c4\ufe0f <b>Board unavailable</b>",
        '<a href="https://jotty.example.com/checklist/b1"><b>Sprint 12</b></a>',
        "It was archived, deleted or is no longer shared with you.",
    ]


def test_deleted_message():
    text = render(
        event(EventKind.DELETED, card_path=("Parent",), actor=None, subtasks=(SubtaskChanges(("Mine",)),)),
        app_url="https://jotty.example.com",
    )
    assert text.splitlines() == [
        "#jotty \U0001f5d1\ufe0f <b>Card deleted</b>",
        '<a href="https://jotty.example.com/checklist/b1"><b>Parent</b></a> · Sprint 12',
        "<blockquote>• Mine</blockquote>",
    ]


def test_mention_message():
    text = render(event(EventKind.MENTION, comment_text="@alice look"))
    assert text.splitlines() == [
        "#jotty \U0001f4ac <b>Mentioned you</b>",
        "<b>Fix login bug</b> · Sprint 12",
        "<blockquote>bob: @alice look</blockquote>",
    ]
