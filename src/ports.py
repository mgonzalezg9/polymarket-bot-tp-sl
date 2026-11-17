from collections.abc import AsyncIterator, Callable, Sequence
from decimal import Decimal
from typing import Protocol

from src.core import Book, Event, GeoblockChecked, MarketInfo
from src.parse import MarketRef


class MarketData(Protocol):
    async def subscribe(self, asset_ids: Sequence[str], publish: Callable[[Event], None]) -> None: ...
    async def get_book(self, asset_id: str) -> Book: ...
    async def resolve(self, ref: MarketRef) -> tuple[MarketInfo, Book]: ...


class Execution(Protocol):
    async def sell_fak(self, request_id: str, asset_id: str, shares: Decimal, floor_price: Decimal, at_ms: int) -> Event: ...
    async def buy_fak(self, request_id: str, asset_id: str, amount_usdc: Decimal, max_price: Decimal, at_ms: int) -> Event: ...
    async def get_order_fill(self, request_id: str, order_id: str, at_ms: int) -> Event: ...
    async def sum_recent_fills(
        self,
        request_id: str,
        asset_id: str,
        side: str,
        since_ms: int,
        exclude_trade_ids: frozenset[str],
        at_ms: int,
    ) -> Event: ...


class Account(Protocol):
    async def load_positions(self, reply_chat_id: int | None, at_ms: int) -> Event: ...
    async def get_balance(self, asset_id: str, at_ms: int) -> Event: ...


class Geoblock(Protocol):
    async def check(self, at_ms: int) -> GeoblockChecked: ...


class Notifier(Protocol):
    async def send(self, chat_id: int | None, text: str, monospace: bool = False) -> None: ...  # None = all whitelisted chats
    def requests(self) -> AsyncIterator[tuple[int, str]]: ...  # (chat_id, text), whitelisted only
