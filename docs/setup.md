# Setup

[← README](../README.md)

The notifier runs as its own compose project next to Jotty's. The examples assume Jotty lives in `/opt/jotty`.

## 1. API keys and Telegram bot

1. In Jotty, each user generates an API key: **Profile → Settings → API Key**.
2. Create a bot with [@BotFather](https://t.me/BotFather). Each user starts a chat with the bot, so it is allowed to
   message them, and looks up their chat id (for example with [@userinfobot](https://t.me/userinfobot)).

## 2. Shared network

Create a network for both projects and attach Jotty to it in `/opt/jotty/compose.yaml`:

```bash
docker network create jotty-net
```

```yaml
services:
  jotty:
    # ...
    networks: [default, jotty-net]

networks:
  jotty-net:
    external: true
```

Recreate Jotty (`docker compose up -d` in `/opt/jotty`). `JOTTY_URL` is then `http://<jotty service name>:3000`,
with the port inside the container, not the published one.

## 3. Configure

```bash
cp compose.example.yaml compose.yaml  # adjust the path to Jotty's data/checklists
cp .env.example .env                  # bot token, URLs, interval
cp users.example.yaml users.yaml      # username, api_key, chat_id per user
mkdir -p state                        # must be writable by uid 1000
```

Mount only Jotty's `data/checklists`: the rest of its data directory holds every user's API key and sessions. The
container runs as uid 1000, which must be able to read the mounted comment files.

`compose.yaml`, `users.yaml` and `.env` are local configuration and git-ignored; `users.yaml` and `.env` hold
secrets. All options: [configuration](configuration.md).

## 4. Start

```bash
docker compose up -d --build
docker compose logs -f
```

The first poll logs `Baseline recorded for <user>` and sends nothing. To test, make changes as another Jotty user:
your own actions do not notify you.

## Troubleshooting

- `Temporary failure in name resolution`: Jotty is not on `jotty-net` or `JOTTY_URL` uses the wrong service name.
- `comment notifications are disabled`: `JOTTY_DATA_DIR` is empty or the volume path is wrong.
- `The API key configured for X belongs to Y`: the key in `users.yaml` belongs to another user.
- Changes to `.env` need `docker compose up -d`; a restart keeps the old environment.
