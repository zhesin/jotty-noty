"""Entry point: python -m noty"""

from __future__ import annotations

import logging
import signal
import sys
import threading

from pydantic import ValidationError

from noty.config import ConfigError, Settings, load_users
from noty.service import build_notifier

log = logging.getLogger("noty")


def _describe(exc: ValidationError) -> str:
    # Leave out input values: a failing users.yaml entry would otherwise print its api_key.
    return "; ".join(
        f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
        for error in exc.errors(include_input=False, include_url=False)
    )


def main() -> int:
    try:
        settings = Settings()
    except ValidationError as exc:
        print(f"Invalid settings: {_describe(exc)}", file=sys.stderr)
        return 2

    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # httpx logs request URLs at INFO, and Telegram URLs contain the bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    try:
        users = load_users(settings.users_file)
    except ConfigError as exc:
        log.error("Invalid users file: %s", exc)
        return 2
    except ValidationError as exc:
        log.error("Invalid users file: %s", _describe(exc))
        return 2

    stop = threading.Event()
    notifier = build_notifier(settings, users, stop)

    def _stop(signum: int, _frame: object) -> None:
        log.info("Received signal %d, stopping", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    notifier.run(settings.poll_interval)
    return 0


if __name__ == "__main__":
    sys.exit(main())
