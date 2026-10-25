# Notifications

[← README](../README.md)

User `U` is notified only when someone else touched a card that is `U`'s: created by `U` or assigned to `U`
(before or after the change). Owning a board grants nothing extra. Mentions reach `U` on any card. Deletions and
vanished boards record no author, so they reach `U` even when `U` did it.

| Message | When |
|---|---|
| 📌 Assigned to you | the assignee became `U`, or a new card appeared already assigned to `U` |
| ✅ Card completed | `completed` went from false to true |
| ✏️ Card updated | title, status, completed, assignee, priority, target date or description changed |
| 💬 New comment | a new comment by someone else |
| 💬 Mentioned you | a new comment by someone else contains `@U`, on any card (replaces "New comment") |
| 🗑️ Card deleted | a card of `U`'s disappeared from a board that is still there |
| 🗄️ Board unavailable | a board with a card of `U`'s disappeared from the list |

## Format

```
#jotty ✏️ Card updated
Parent → Card · Board
┃ status: To Do → In Progress
by bob
```

- `#jotty` is a clickable hashtag that filters the chat down to these messages.
- The card name is bold and links to its board when `APP_URL` is set.
- Changes, sub-cards and comment text are quoted; descriptions are reported as "updated" without their text.
- Long messages are cut to Telegram's limit with "… and N more".

## Grouping

When several changes hit one card in one poll, the header is chosen as 📌 → ✅ → ✏️ and all changed fields are
listed in one message. Updates and completions of sub-cards under the same top-level card by the same person are
folded into one message for that card (moving a parent to an auto-complete column touches every sub-card).
Assignments and comments are always separate.

A new card created by `U` that someone else already edited before the next poll is reported as "Card updated"
without a list of changes.

## Mentions

A mention is `@` followed by the exact, case-sensitive username, as Jotty's mention picker inserts it, at the start
of the comment or after whitespace, and not followed by more of a longer name (`@bob` is not found in `@bobby` or
`@bob.smith`). Usernames containing `@`, as SSO often creates them (`name@domain`), work too.

## Deleted cards

A card that was on a board in the previous poll and is gone now is reported to its creator and assignee. Deleting a
card deletes its sub-cards: `U` gets one message for the topmost deleted card of `U`'s, with `U`'s deleted cards
beneath it listed. A card with a positional id counts as deleted only when no card with the same text and parents
is left.

## Not reported

- Edited and deleted comments.
- Archiving a single card: the API returns archived cards like any other, without the flag, and archiving does not
  change their status or stamp.
- Anything before the first run's baseline.

See also [limitations of polling](how-it-works.md#delivery).
