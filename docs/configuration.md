# Configuration

[← README](../README.md)

## Environment (`.env`)

| Variable | Default | Description |
|---|---|---|
| `JOTTY_URL` | required | Jotty URL reachable from the container, e.g. `http://jotty:3000` |
| `TELEGRAM_BOT_TOKEN` | required | Bot token |
| `APP_URL` | empty | Public Jotty URL; card names link to `APP_URL/checklist/<boardUuid>` |
| `POLL_INTERVAL` | `60` | Seconds between polls (minimum 5) |
| `JOTTY_DATA_DIR` | empty | Mount point whose `checklists/` is Jotty's `data/checklists`; comments are disabled when empty |
| `BOARD_RETENTION_DAYS` | `30` | How long a [missing board](how-it-works.md#missing-boards) is remembered |
| `USERS_FILE` | `/app/users.yaml` | User list |
| `STATE_DIR` | `/app/state` | Snapshot directory |
| `REQUEST_TIMEOUT` | `15` | HTTP timeout in seconds |
| `LOG_LEVEL` | `INFO` | Python log level; `DEBUG` logs every poll |

## Users (`users.yaml`)

A list of `{username, api_key, chat_id}`; `chat_id` is a number or an `@channel` name. See
[users.example.yaml](../users.example.yaml). Each API key must belong to its username; this is checked on the first poll and after a rejected key.

## Health check

The container has a health check (`python -m noty.healthcheck`). `STATE_DIR/.heartbeat` is touched after every
successful user poll and every delivered message; the container turns unhealthy when it is older than three
intervals plus five minutes, which happens when no user can be polled (Jotty down, every key rejected).
