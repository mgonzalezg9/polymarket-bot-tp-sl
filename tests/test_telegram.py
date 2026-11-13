import asyncio

import httpx
import pytest

from constants import TELEGRAM_SEND_MAX_ATTEMPTS
from src.telegram import TelegramNotifier


def post_attempts(failures: list[Exception]) -> int:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts <= len(failures):
            raise failures[attempts - 1]
        return httpx.Response(200, json={"ok": True})

    async def send() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await TelegramNotifier("token", (1,), client).send(None, "hi")

    asyncio.run(send())
    return attempts


@pytest.fixture(autouse=True)
def no_retry_delay(monkeypatch):
    monkeypatch.setattr("src.telegram.TELEGRAM_RETRY_SECONDS", 0)


def test_send_resends_after_failed_connects():
    assert post_attempts([httpx.ConnectError("down"), httpx.ConnectTimeout("slow")]) == 3


def test_send_gives_up_after_max_attempts():
    assert post_attempts([httpx.ConnectError("down")] * 10) == TELEGRAM_SEND_MAX_ATTEMPTS


def test_send_never_resends_once_the_request_may_have_arrived():
    assert post_attempts([httpx.ReadTimeout("slow")]) == 1
