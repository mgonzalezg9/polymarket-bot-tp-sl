import asyncio
import logging
from collections.abc import AsyncIterator, Sequence

import httpx

from constants import (
    READ_TIMEOUT_SECONDS,
    TELEGRAM_LONG_POLL_SECONDS,
    TELEGRAM_MAX_MESSAGE_LENGTH,
    TELEGRAM_RETRY_SECONDS,
)
from src.format import EMOJI_BACKLOG

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"


def split_message(text: str) -> list[str]:
    return [text[start : start + TELEGRAM_MAX_MESSAGE_LENGTH] for start in range(0, len(text), TELEGRAM_MAX_MESSAGE_LENGTH)]


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_ids: Sequence[int], client: httpx.AsyncClient) -> None:
        self._base_url = f"{TELEGRAM_API_BASE}/bot{bot_token}"
        self._chat_ids = tuple(chat_ids)
        self._client = client

    async def send(self, chat_id: int | None, text: str) -> None:
        for target in self._chat_ids if chat_id is None else (chat_id,):
            for chunk in split_message(text):
                try:
                    response = await self._client.post(
                        f"{self._base_url}/sendMessage",
                        json={"chat_id": target, "text": chunk},
                        timeout=READ_TIMEOUT_SECONDS,
                    )
                    if response.status_code != 200:
                        logger.warning("sendMessage to %s failed: %s %s", target, response.status_code, response.text)
                # Never log the exception's request URL: it contains the bot token.
                except Exception as error:
                    logger.warning("sendMessage to %s failed: %s", target, type(error).__name__)

    async def requests(self) -> AsyncIterator[tuple[int, str]]:
        offset = await self._skip_backlog()
        while True:
            updates = await self._get_updates(offset, TELEGRAM_LONG_POLL_SECONDS)
            if updates is None:
                await asyncio.sleep(TELEGRAM_RETRY_SECONDS)
                continue
            for update in updates:
                offset = update["update_id"] + 1
                chat_id, text = _message(update)
                if chat_id not in self._chat_ids:
                    logger.warning("Ignored message from non-whitelisted chat %s", chat_id)
                elif text:
                    yield chat_id, text

    async def _skip_backlog(self) -> int | None:
        # offset=-1 returns only the newest update and drops the older ones; retry until it succeeds,
        # otherwise the next poll would replay the backlog.
        while (updates := await self._get_updates(-1, 0)) is None:
            await asyncio.sleep(TELEGRAM_RETRY_SECONDS)
        if not updates:
            return None
        chat_id, _ = _message(updates[-1])
        if chat_id in self._chat_ids:
            await self.send(chat_id, f"{EMOJI_BACKLOG} Ignored messages queued while offline")
        return updates[-1]["update_id"] + 1

    async def _get_updates(self, offset: int | None, timeout_seconds: int) -> list[dict] | None:
        params = {"timeout": timeout_seconds} if offset is None else {"timeout": timeout_seconds, "offset": offset}
        try:
            response = await self._client.get(
                f"{self._base_url}/getUpdates", params=params, timeout=timeout_seconds + READ_TIMEOUT_SECONDS
            )
            body = response.json()
            if response.status_code != 200 or not body.get("ok"):
                logger.warning("getUpdates failed: %s %s", response.status_code, body.get("description"))
                return None
            return body["result"]
        except Exception as error:
            logger.warning("getUpdates failed: %s", type(error).__name__)
            return None


def _message(update: dict) -> tuple[int | None, str]:
    message = update.get("message") or {}
    return message.get("chat", {}).get("id"), (message.get("text") or "").strip()


class StdoutNotifier:
    async def send(self, chat_id: int | None, text: str) -> None:
        print(f"[{'all' if chat_id is None else chat_id}] {text}")

    async def requests(self) -> AsyncIterator[tuple[int, str]]:
        return
        yield  # unreachable: makes this an empty async generator
