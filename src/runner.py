import asyncio
import logging
from collections import Counter
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from polymarket.errors import PolymarketError

from constants import EXIT_FLUSH_SECONDS, ORDER_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS, TICK_SECONDS
from src.core import (
    BalanceFetchFailed,
    BookFetchFailed,
    BookUpdated,
    CheckFailed,
    CheckGeoblock,
    Command,
    CoreConfig,
    Event,
    Exit,
    FetchBalance,
    FetchBook,
    FetchPositions,
    GeoblockChecked,
    Notify,
    OrderOutcomeUnknown,
    PollOrder,
    PositionsFetchFailed,
    ReconcileFills,
    SendBuyOrder,
    SendSellOrder,
    Started,
    State,
    Subscribe,
    Ticked,
    UserRequestReceived,
    step,
)
from src.format import EMOJI_CRASH, EMOJI_FAILED, market_label
from src.parse import ParseError, parse_request
from src.ports import Account, Execution, Geoblock, MarketData, Notifier

logger = logging.getLogger(__name__)

REPLAY_CHAT_ID = 1


@dataclass(frozen=True)
class Adapters:
    market_data: MarketData
    execution: Execution
    account: Account
    geoblock: Geoblock
    notifier: Notifier


def describe(error: BaseException) -> str:
    return str(error) or type(error).__name__


async def request_event(
    adapters: Adapters, chat_id: int, text: str, now_ms: Callable[[], int]
) -> UserRequestReceived | None:
    request = parse_request(text)
    if isinstance(request, ParseError):
        await adapters.notifier.send(chat_id, f"{EMOJI_FAILED} {request.message}")
        return None
    ref = getattr(request, "market", None)
    if ref is None:
        return UserRequestReceived(now_ms(), chat_id, request)
    try:
        market, book = await asyncio.wait_for(adapters.market_data.resolve(ref), READ_TIMEOUT_SECONDS)
    except Exception as error:
        logger.warning("Could not resolve %s: %r", ref, error)
        await adapters.notifier.send(chat_id, f"{EMOJI_FAILED} {describe(error)}")
        return None
    return UserRequestReceived(now_ms(), chat_id, request, market, book)


class EventLoop:
    """Single consumer of the event queue; runs the core's commands as tasks feeding results back."""

    def __init__(self, adapters: Adapters, core_config: CoreConfig, now_ms: Callable[[], int]) -> None:
        self.adapters = adapters
        self.now_ms = now_ms
        self.state = State(config=core_config)
        self.queue: asyncio.Queue[Event] = asyncio.Queue()
        self.tasks: set[asyncio.Task] = set()
        self.last_notification: asyncio.Task | None = None

    def handle(self, event: Event) -> int | None:
        logger.debug("Event %s", type(event).__name__)
        self.state, commands = step(self.state, event)
        exit_code = None
        for command in commands:
            logger.debug("Command %s", type(command).__name__)
            if isinstance(command, Exit):
                exit_code = command.code
            else:
                self.execute(command)
        return exit_code

    def execute(self, command: Command) -> None:
        adapters, at_ms = self.adapters, self.now_ms()
        match command:
            case Notify():
                self.notify(command.chat_id, command.text, command.monospace)
            case CheckGeoblock():
                self.call(
                    adapters.geoblock.check(at_ms),
                    READ_TIMEOUT_SECONDS,
                    lambda error: GeoblockChecked(at_ms, blocked=False, country="", error=error),
                )
            case FetchPositions():
                self.call(
                    adapters.account.load_positions(command.reply_chat_id, at_ms),
                    READ_TIMEOUT_SECONDS,
                    lambda error: PositionsFetchFailed(at_ms, error, command.reply_chat_id),
                )
            case FetchBook():
                self.call(
                    fetch_book(adapters.market_data, command.asset_id, at_ms),
                    READ_TIMEOUT_SECONDS,
                    lambda error: BookFetchFailed(at_ms, command.asset_id, error),
                )
            case FetchBalance():
                self.call(
                    adapters.account.get_balance(command.asset_id, at_ms),
                    READ_TIMEOUT_SECONDS,
                    lambda error: BalanceFetchFailed(at_ms, command.asset_id, error),
                )
            case Subscribe():
                self.spawn(adapters.market_data.subscribe(command.asset_ids, self.queue.put_nowait))
            case SendSellOrder():
                self.call(
                    adapters.execution.sell_fak(command.request_id, command.asset_id, command.shares, command.floor_price, at_ms),
                    ORDER_TIMEOUT_SECONDS,
                    lambda error: OrderOutcomeUnknown(at_ms, command.request_id, error),
                )
            case SendBuyOrder():
                self.call(
                    adapters.execution.buy_fak(command.request_id, command.asset_id, command.amount_usdc, command.max_price, at_ms),
                    ORDER_TIMEOUT_SECONDS,
                    lambda error: OrderOutcomeUnknown(at_ms, command.request_id, error),
                )
            case PollOrder():
                self.call(
                    adapters.execution.get_order_fill(command.request_id, command.order_id, at_ms),
                    READ_TIMEOUT_SECONDS,
                    lambda error: CheckFailed(at_ms, command.request_id, error),
                )
            case ReconcileFills():
                self.call(
                    adapters.execution.sum_recent_fills(
                        command.request_id, command.asset_id, command.side, command.since_ms, command.exclude_trade_ids, at_ms
                    ),
                    READ_TIMEOUT_SECONDS,
                    lambda error: CheckFailed(at_ms, command.request_id, error),
                )

    def spawn(self, coroutine: Coroutine[Any, Any, Any]) -> asyncio.Task:
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self._forget)
        return task

    def _forget(self, task: asyncio.Task) -> None:
        self.tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error("Task %s failed", task.get_coro().__qualname__, exc_info=task.exception())

    def call(self, coroutine: Coroutine[Any, Any, Event], timeout: float, on_error: Callable[[str], Event]) -> None:
        self.spawn(self._put_result(coroutine, timeout, on_error))

    async def _put_result(self, coroutine: Coroutine[Any, Any, Event], timeout: float, on_error: Callable[[str], Event]) -> None:
        name = coroutine.__qualname__
        try:
            event = await asyncio.wait_for(coroutine, timeout)
        except Exception as error:
            logger.warning("%s failed: %r", name, error, exc_info=not isinstance(error, (TimeoutError, PolymarketError)))
            event = on_error(describe(error))
        self.queue.put_nowait(event)

    def notify(self, chat_id: int | None, text: str, monospace: bool = False) -> None:
        logger.info("Notify %s: %s", "all" if chat_id is None else chat_id, text)
        self.last_notification = self.spawn(self._send_in_order(self.last_notification, chat_id, text, monospace))

    async def _send_in_order(
        self, previous: asyncio.Task | None, chat_id: int | None, text: str, monospace: bool
    ) -> None:
        if previous is not None:
            await asyncio.wait([previous])
        await self.adapters.notifier.send(chat_id, text, monospace)

    async def settle(self) -> int | None:
        """Replay only: step every queued event and await its commands before the next one."""
        while True:
            while pending := [task for task in self.tasks if not task.done()]:
                await asyncio.gather(*pending, return_exceptions=True)
            if self.queue.empty():
                return None
            if (exit_code := self.handle(self.queue.get_nowait())) is not None:
                return exit_code

    async def feed(self, *events: Event | None) -> int | None:
        exit_code = await self.settle()
        for event in events:
            if exit_code is not None:
                break
            if event is not None:
                self.queue.put_nowait(event)
                exit_code = await self.settle()
        return exit_code

    async def close(self) -> None:
        if self.last_notification is not None:
            await asyncio.wait([self.last_notification], timeout=EXIT_FLUSH_SECONDS)
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def fetch_book(market_data: MarketData, asset_id: str, at_ms: int) -> Event:
    return BookUpdated(at_ms, asset_id, await market_data.get_book(asset_id))


async def tick(queue: asyncio.Queue[Event], now_ms: Callable[[], int]) -> None:
    while True:
        await asyncio.sleep(TICK_SECONDS)
        queue.put_nowait(Ticked(now_ms()))


async def forward_requests(adapters: Adapters, queue: asyncio.Queue[Event], now_ms: Callable[[], int]) -> None:
    async for chat_id, text in adapters.notifier.requests():
        if (event := await request_event(adapters, chat_id, text, now_ms)) is not None:
            queue.put_nowait(event)


async def run_live(adapters: Adapters, core_config: CoreConfig, now_ms: Callable[[], int]) -> int:
    loop = EventLoop(adapters, core_config, now_ms)
    loop.queue.put_nowait(Started(now_ms()))
    loop.spawn(tick(loop.queue, now_ms))
    loop.spawn(forward_requests(adapters, loop.queue, now_ms))
    try:
        while True:
            if (exit_code := loop.handle(await loop.queue.get())) is not None:
                return exit_code
    except Exception as error:
        logger.exception("Event loop crashed")
        loop.notify(None, f"{EMOJI_CRASH} Crash: {describe(error)}")
        return 1
    finally:
        await loop.close()


async def run_replay(adapters: Adapters, core_config: CoreConfig, replay_lines: list[dict]) -> int:
    paper = adapters.market_data  # a PaperExchange, which also loads the replay lines
    current_ms = next((line["ts"] for line in replay_lines if "ts" in line), 0)
    loop = EventLoop(adapters, core_config, lambda: current_ms)
    # Started is stepped at the first timed line, once the seed lines above it are loaded.
    loop.queue.put_nowait(Started(current_ms))
    try:
        for line in replay_lines:
            book = paper.load_line(line)
            if "ts" not in line:
                continue
            current_ms = line["ts"]
            event: Event | None = None
            if line["type"] == "book":
                event = BookUpdated(current_ms, line["asset_id"], book)
            elif line["type"] == "command":
                event = await request_event(adapters, REPLAY_CHAT_ID, line["text"], loop.now_ms)
            if (exit_code := await loop.feed(Ticked(current_ms), event)) is not None:
                return exit_code
        if (exit_code := await loop.feed()) is not None:
            return exit_code
    finally:
        await loop.close()
    print_summary(loop.state)
    return 0


def print_summary(state: State) -> None:
    triggers = sorted(state.triggers.values(), key=lambda trigger: trigger.trigger_id)
    counts = Counter(trigger.status for trigger in triggers)
    by_status = ", ".join(f"{status} {count}" for status, count in counts.items())
    print(f"\nReplay finished. Triggers: {len(triggers)}" + (f" ({by_status})" if by_status else ""))
    for trigger in triggers:
        label = market_label(state.markets.get(trigger.asset_id), trigger.asset_id)
        print(
            f"  #{trigger.trigger_id} {trigger.kind} {label} {trigger.status}: "
            f"sold {trigger.filled_shares} shares for {trigger.proceeds_usdc} USDC"
        )
    print(f"Positions: {len(state.positions)}")
    for position in state.positions.values():
        label = market_label(state.markets.get(position.asset_id), position.asset_id)
        print(f"  {label}: {position.size} shares @ {position.avg_price}")
    print(f"USDC: {state.collateral_usdc}")
