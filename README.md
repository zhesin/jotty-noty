# jotty-noty

Telegram notifications for [Jotty](https://github.com/fccview/jotty) Kanban boards.

Jotty has no webhooks, so jotty-noty polls its REST API for each configured user, compares the boards with the
previous poll and sends a Telegram message when someone else:

- assigns a card to you, or changes or completes a card you created or that is assigned to you,
- comments on such a card, or mentions you (`@username`) in a comment on any card,
- deletes your card, or a board with your cards becomes unavailable.

```
#jotty ✏️ Card updated
Fix login bug · Sprint 12
┃ status: To Do → In Progress
by bob
```

## How it works

1. Every `POLL_INTERVAL` seconds, for each user in `users.yaml`, the boards are read through the API with that
   user's key, so each user sees exactly what Jotty shows them.
2. Comments, which the API does not expose, are read from Jotty's `data/checklists`, mounted read-only.
3. The result is compared with the user's saved snapshot; the differences that concern the user and were made by
   someone else become messages. The first run only records a baseline.

Details: [how it works](docs/how-it-works.md) · [notifications](docs/notifications.md)

## Quick start

```bash
cp compose.example.yaml compose.yaml   # path to Jotty's data/checklists, shared network
cp .env.example .env                   # bot token, Jotty URLs
cp users.example.yaml users.yaml       # username, API key and Telegram chat id per user
mkdir -p state
docker compose up -d --build
```

Full guide: [setup](docs/setup.md) · [configuration](docs/configuration.md) · [development](docs/development.md)

## License

[MIT](LICENSE)
