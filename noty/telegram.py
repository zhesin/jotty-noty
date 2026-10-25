"""Minimal Telegram Bot API sender (sendMessage only)."""

from __future__ import annotations

import logging
import time
from typing import Callable

import httpx

log = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"
# Attempts for network errors and 5xx; rate limits (429) are governed by MAX_RATE_LIMIT_WAIT instead.
MAX_ATTEMPTS = 3
# Telegram asks bots to stay around one message per second per chat.
MIN_INTERVAL = 1.0
MAX_RATE_LIMIT_WAIT = 120.0

# Sleeps for the given seconds; returns True when the wait was interrupted (shutdown).
Sleeper = Callable[[float], bool]


def _blocking_sleep(seconds: float) -> bool:
    time.sleep(seconds)
    return False


class TelegramClient:
    def __init__(
        self,
        token: str,
        timeout: float = 15.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Sleeper = _blocking_sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._token = token
        self._sleep = sleep
        self._clock = clock
        self._last_sent: dict[str, float] = {}
        self._http = httpx.Client(base_url=API_BASE, timeout=timeout, transport=transport)

    def close(self) -> None:
        self._http.close()

    def _redact(self, text: str) -> str:
        return text.replace(self._token, "<token>")

    def _wait(self, seconds: float) -> bool:
        """Wait unless interrupted; returns False when the caller should give up."""
        return seconds <= 0 or not self._sleep(seconds)

    def send(self, chat_id: int | str, text: str) -> bool:
        """Send an HTML message. Retries on rate limits, 5xx and network errors; returns success."""
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }
        chat = str(chat_id)
        last = self._last_sent.get(chat)
        if last is not None and not self._wait(last + MIN_INTERVAL - self._clock()):
            return False

        attempts = 0
        rate_limited_for = 0.0
        while True:
            try:
                response = self._http.post(f"/bot{self._token}/sendMessage", json=payload)
            except httpx.HTTPError as exc:
                attempts += 1
                log.warning("Telegram request failed (attempt %d): %s", attempts, self._redact(f"{type(exc).__name__}: {exc}"))
                if attempts >= MAX_ATTEMPTS or not self._wait(2 ** attempts):
                    break
                continue

            self._last_sent[chat] = self._clock()
            if response.status_code == 200:
                return True

            try:
                body = response.json()
            except ValueError:
                body = {}
            if not isinstance(body, dict):
                body = {}
            description = self._redact(str(body.get("description", response.text[:200])))

            if response.status_code == 429:
                parameters = body.get("parameters")
                retry_after = float(parameters.get("retry_after", 5)) if isinstance(parameters, dict) else 5.0
                if rate_limited_for + retry_after > MAX_RATE_LIMIT_WAIT:
                    log.error("Telegram rate limit for chat %s lasts too long, dropping the message", chat_id)
                    return False
                rate_limited_for += retry_after
                log.warning("Telegram rate limit, retrying in %ss", retry_after)
                if not self._wait(retry_after):
                    break
                continue

            if response.status_code >= 500:
                attempts += 1
                log.warning("Telegram HTTP %d (attempt %d): %s", response.status_code, attempts, description)
                if attempts >= MAX_ATTEMPTS or not self._wait(2 ** attempts):
                    break
                continue

            # 4xx other than 429 will not succeed on retry (bad chat id, bot blocked, bad HTML).
            log.error("Telegram rejected message to chat %s: HTTP %d %s", chat_id, response.status_code, description)
            return False

        log.error("Giving up sending to chat %s", chat_id)
        return False
