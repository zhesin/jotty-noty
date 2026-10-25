# How it works

[← README](../README.md)

Jotty has no webhooks, so the notifier polls every `POLL_INTERVAL` seconds. For each user, with that user's API key:

1. Once per user (and again after a `401`), `GET /api/user` confirms the API key belongs to the configured username.
2. Comments of the boards already known are read from Jotty's data directory (see [Comments](#comments)).
3. `GET /api/checklists?type=kanban` lists every board the user owns or that is shared with them. Cards here carry
   `createdBy`, `lastModifiedBy`, `lastModifiedAt`, status, dates, priority and description, but no assignee.
4. `GET /api/kanban/{boardId}` gives each card's `assignee` and the board's column labels. Cards from both calls
   are merged by card id; sub-cards are tracked like top-level cards. This call is skipped for boards that did
   not change (see [Unchanged boards](#unchanged-boards)).
5. The result is compared with the user's previous snapshot (`STATE_DIR/<username>.json`) and the differences
   become [notifications](notifications.md). The new snapshot is saved before any message is sent.

The first run, and any run after the state file is lost, only records a silent baseline.

## Comments

The REST API does not expose comments. They are read from `data/checklists/<owner>/.comments/<boardUuid>.json`,
mounted read-only. They are read before the boards, so a comment is always compared with a board state at least as
new: a comment on a card created or reassigned during the poll is not lost. Without the mount, comment and mention
notifications are disabled.

## Unchanged boards

A board's `updatedAt` is its file's mtime. When it equals the one saved last time, the board did not change: the
detail call is skipped and the saved cards are reused.

This relies on every write changing the mtime. On filesystems with coarse timestamps (FAT, some NFS, CIFS or FUSE
mounts) a second write within the same tick can go unnoticed until the board is written again. Keep Jotty's data on
a local filesystem such as ext4, xfs or btrfs.

## Inconsistent reads

If `updatedAt` differs between the list and detail calls, the board changed in between and is read again on the
next poll. After three such polls in a row it is accepted as read (Jotty can rewrite a due recurring card of a
shared board on every read), except for assignee changes the card's stamp does not confirm yet. The first run's
baseline accepts it right away. Up to 1 ms of difference counts as equal: with GNU `find` the two calls round the
same mtime differently.

A board whose detail call fails keeps its previous state until it can be read again; only a failure of the board
list skips the user's whole poll.

## Who made the change

For a card it is `lastModifiedBy`, but only when `lastModifiedAt` moved too. Jotty changes some cards without
stamping them: a parent completed by its last open sub-card, children completed with their parent, a parent
recomputed after a sub-card was added or deleted, and recurring cards reset to their first column. For those, in
order:

1. the most recently re-stamped sub-card,
2. the most recently re-stamped parent card,
3. the card's last known `lastModifiedBy` (a recurring reset is reported "by" whoever closed the card, and not at
   all when that was you).

For a comment the actor is its author. Deletions and vanished boards have no recorded actor.

## Missing boards

A board that disappears from the list (archived, deleted or unshared) is remembered for `BOARD_RETENTION_DAYS`. If
it comes back in that time it is compared with its last known state, so assignments are not announced again and
comments posted meanwhile are sent. A board seen for the first time (newly shared, copied, or back after the
retention period) only announces open cards assigned to you by someone else; its comments are taken as already
seen.

## Positional ids

Items in a board file edited outside Jotty may have positional ids (`<file>-<level>-<n>`) that shift when lines are
inserted. Such cards are matched by their text and parents where possible; duplicate texts can still cause a wrong
match until Jotty saves the board and the ids become stable.

## Delivery

Delivery is at most once: the snapshot is saved first, so a message that fails after retries, or is pending during
shutdown, is logged and dropped rather than repeated. Telegram is sent at most about one message per second per
chat; a rate limit longer than two minutes drops the message.

If a card changes several times between polls, only its final state and last editor are seen.
