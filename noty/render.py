"""Format events as Telegram messages (HTML parse mode, one emoji per message).

    #jotty ✏️ Card updated
    Parent → Card · Board        (card in bold, linked to the board)
    ┃ status: To Do → In Progress (changes, sub-cards or the comment, in a quote)
    by bob
"""

from __future__ import annotations

from html import escape

from noty.models import Change, Event, EventKind

HEADERS: dict[EventKind, tuple[str, str]] = {
    EventKind.ASSIGNED: ("\U0001f4cc", "Assigned to you"),
    EventKind.COMPLETED: ("✅", "Card completed"),
    EventKind.UPDATED: ("✏️", "Card updated"),
    EventKind.COMMENT: ("\U0001f4ac", "New comment"),
    EventKind.MENTION: ("\U0001f4ac", "Mentioned you"),
    EventKind.DELETED: ("\U0001f5d1\ufe0f", "Card deleted"),
    EventKind.BOARD_GONE: ("\U0001f5c4\ufe0f", "Board unavailable"),
}

# Leads every message so they stand out among other services' messages in the same chat;
# Telegram makes it a clickable hashtag that filters the chat down to these messages.
TAG = "#jotty"
ARROW = " → "
# Kinds that quote a comment, whose author is already named in the quote.
_COMMENT_KINDS = (EventKind.COMMENT, EventKind.MENTION)
SEPARATOR = " · "
MAX_VALUE_LENGTH = 200
MAX_PATH_LENGTH = 600
MAX_COMMENT_LENGTH = 1000
MAX_COMMENT_LINES = 15
# Telegram's sendMessage limit, counted in UTF-16 code units after entity parsing;
# measuring the raw HTML is a safe upper bound.
MAX_MESSAGE_LENGTH = 4096


def telegram_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _clip_multiline(text: str) -> str:
    lines = [" ".join(line.split()) for line in text.strip().splitlines()]
    lines = [line for line in lines if line] or [""]
    clipped = "\n".join(lines[:MAX_COMMENT_LINES])
    if len(lines) > MAX_COMMENT_LINES or len(clipped) > MAX_COMMENT_LENGTH:
        clipped = clipped[: MAX_COMMENT_LENGTH - 1].rstrip() + "…"
    return clipped


def _value(value: str | None) -> str:
    return escape(_clip(value, MAX_VALUE_LENGTH)) if value else "none"


def _change(change: Change) -> str:
    if change.old is None and change.new is None:
        return f"{escape(change.field)}: updated"
    return f"{escape(change.field)}: {_value(change.old)}{ARROW}{_value(change.new)}"


def _path(parts: tuple[str, ...]) -> str:
    return escape(_clip(ARROW.join(_clip(part, MAX_VALUE_LENGTH) for part in parts), MAX_PATH_LENGTH))


def _body(event: Event) -> list[str]:
    """Lines shown in the quote."""
    lines = [_change(change) for change in event.changes]
    for subtask in event.subtasks:
        if event.kind is EventKind.DELETED:
            lines.append(f"• {_path(subtask.path)}")
            continue
        details = ", ".join(_change(change) for change in subtask.changes) or "updated"
        lines.append(f"• {_path(subtask.path)}: {details}")
    if event.kind in _COMMENT_KINDS:
        author = escape(event.actor or "someone")
        lines.append(f"{author}: {escape(_clip_multiline(event.comment_text or ''))}")
    return lines


def _fit(head: list[str], body: list[str], tail: list[str]) -> str:
    """Drop body lines from the end until the message fits, noting how many were left out."""
    def assemble(lines: list[str], dropped: int) -> str:
        quoted = lines + ([f"… and {dropped} more"] if dropped else [])
        quote = ["<blockquote>" + "\n".join(quoted) + "</blockquote>"] if quoted else []
        return "\n".join(head + quote + tail)

    message = assemble(body, 0)
    kept = len(body)
    while telegram_length(message) > MAX_MESSAGE_LENGTH and kept > 0:
        kept -= 1
        message = assemble(body[:kept], len(body) - kept)
    return message


def _subject(event: Event, app_url: str | None) -> str:
    """The card (or, for a board event, the board) in bold, linked to the board, then the board's name."""
    board = escape(_clip(event.board_title, MAX_VALUE_LENGTH))
    name = f"<b>{_path(event.card_path)}</b>" if event.card_path else f"<b>{board}</b>"
    if app_url:
        href = escape(f"{app_url.rstrip('/')}/checklist/{event.board_uuid}", quote=True)
        name = f'<a href="{href}">{name}</a>'
    return f"{name}{SEPARATOR}{board}" if event.card_path else name


def render(event: Event, app_url: str | None = None) -> str:
    emoji, title = HEADERS[event.kind]
    head = [f"{TAG} {emoji} <b>{title}</b>", _subject(event, app_url)]

    tail: list[str] = []
    if event.kind is EventKind.BOARD_GONE:
        tail.append("It was archived, deleted or is no longer shared with you.")
    if event.kind not in _COMMENT_KINDS and event.actor:
        tail.append(f"by {escape(event.actor)}")

    return _fit(head, _body(event), tail)
