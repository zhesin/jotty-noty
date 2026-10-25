import json
import logging

import httpx

from noty.telegram import MAX_ATTEMPTS, MAX_RATE_LIMIT_WAIT, MIN_INTERVAL, TelegramClient

TOKEN = "123:SECRET"


class FakeTime:
    """Clock and sleeper in one: sleeping advances the clock; can simulate a shutdown."""

    def __init__(self, interrupt: bool = False):
        self.now = 1000.0
        self.sleeps: list[float] = []
        self.interrupt = interrupt

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> bool:
        self.sleeps.append(seconds)
        self.now += seconds
        return self.interrupt


def make(handler, interrupt: bool = False):
    fake = FakeTime(interrupt)
    client = TelegramClient(TOKEN, transport=httpx.MockTransport(handler), sleep=fake.sleep, clock=fake.clock)
    return client, fake


def ok(request):
    return httpx.Response(200, json={"ok": True})


def test_sends_html_message():
    requests = []

    def handler(request):
        requests.append(request)
        return ok(request)

    client, fake = make(handler)
    assert client.send(42, "<b>hi</b>") is True

    [request] = requests
    assert request.url.path == f"/bot{TOKEN}/sendMessage"
    assert json.loads(request.content) == {
        "chat_id": 42,
        "text": "<b>hi</b>",
        "parse_mode": "HTML",
        "link_preview_options": {"is_disabled": True},
    }
    assert fake.sleeps == []


def test_paces_messages_to_the_same_chat_only():
    client, fake = make(ok)
    client.send(1, "a")
    client.send(2, "b")
    assert fake.sleeps == []
    fake.now += 0.25
    client.send(1, "c")
    assert fake.sleeps == [MIN_INTERVAL - 0.25]


def test_rate_limit_waits_retry_after_without_using_attempts():
    responses = iter(
        [httpx.Response(429, json={"ok": False, "parameters": {"retry_after": 7}})] * 5
        + [httpx.Response(200, json={"ok": True})]
    )
    client, fake = make(lambda request: next(responses))
    assert client.send(1, "x") is True
    assert fake.sleeps == [7.0] * 5


def test_rate_limit_gives_up_after_max_wait():
    client, fake = make(lambda request: httpx.Response(429, json={"parameters": {"retry_after": 50}}))
    assert client.send(1, "x") is False
    assert sum(fake.sleeps) <= MAX_RATE_LIMIT_WAIT


def test_client_error_is_not_retried():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(400, json={"ok": False, "description": "Bad Request: chat not found"})

    client, fake = make(handler)
    assert client.send(1, "x") is False
    assert len(calls) == 1 and fake.sleeps == []


def test_gives_up_after_repeated_failures_without_a_final_sleep():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ConnectError("down")

    client, fake = make(handler)
    assert client.send(1, "x") is False
    assert len(calls) == MAX_ATTEMPTS
    assert fake.sleeps == [2, 4]


def test_server_error_then_success():
    responses = iter([httpx.Response(502, text="bad gateway"), httpx.Response(200, json={"ok": True})])
    client, fake = make(lambda request: next(responses))
    assert client.send(1, "x") is True
    assert fake.sleeps == [2]


def test_shutdown_interrupts_waiting():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, json={"parameters": {"retry_after": 30}})

    client, fake = make(handler, interrupt=True)
    assert client.send(1, "x") is False
    assert len(calls) == 1


def test_token_never_logged(caplog):
    def handler(request):
        raise httpx.ConnectError(f"failed for https://api.telegram.org/bot{TOKEN}/sendMessage")

    client, _ = make(handler)
    with caplog.at_level(logging.DEBUG):
        client.send(1, "x")
    assert TOKEN not in caplog.text
    assert "<token>" in caplog.text
