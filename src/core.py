from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from enum import StrEnum
from typing import Literal

from src.parse import UserRequest


class BotStatus(StrEnum):
    STARTING = "STARTING"
    GEOBLOCK_CHECK = "GEOBLOCK_CHECK"
    RECONCILING = "RECONCILING"
    RUNNING = "RUNNING"
    STALE = "STALE"
    HALTED = "HALTED"


class TriggerKind(StrEnum):
    STOP_LOSS = "SL"
    TAKE_PROFIT = "TP"


class TriggerStatus(StrEnum):
    ARMED = "ARMED"
    TRIGGERED = "TRIGGERED"
    ORDER_PENDING = "ORDER_PENDING"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class GapPolicy(StrEnum):
    SELL = "sell"
    ALERT = "alert"


@dataclass(frozen=True)
class MarketInfo:
    asset_id: str
    slug: str  # "" when resolved from a bare asset id
    outcome: str
    tick_size: Decimal
    min_order_size: Decimal
    is_v2: bool


@dataclass(frozen=True)
class Book:
    best_bid: Decimal | None
    best_bid_size: Decimal | None
    best_ask: Decimal | None
    best_ask_size: Decimal | None
    last_trade_price: Decimal | None
    received_at_ms: int


@dataclass(frozen=True)
class Position:
    asset_id: str
    size: Decimal
    avg_price: Decimal


@dataclass(frozen=True)
class TriggerOrder:
    trigger_id: int
    kind: TriggerKind
    asset_id: str
    trigger_price: Decimal
    floor_price: Decimal
    size: Decimal | None  # None = whole position at fire time
    policy: GapPolicy
    status: TriggerStatus = TriggerStatus.ARMED
    filled_shares: Decimal = Decimal(0)
    proceeds_usdc: Decimal = Decimal(0)
    credited_trade_ids: frozenset[str] = frozenset()
    waiting_notified: bool = False
    close_reason: str | None = None


@dataclass(frozen=True)
class PendingOrder:
    request_id: str
    asset_id: str
    side: Literal["BUY", "SELL"]
    sent_at_ms: int
    trigger_id: int | None  # None for /open and /close
    reply_chat_id: int | None
    order_id: str | None = None  # known once the CLOB answered "delayed"
    outcome_unknown: bool = False
    last_check_ms: int = 0


@dataclass(frozen=True)
class CoreConfig:
    stale_ms: int
    heartbeat_ms: int  # 0 = off
    close_slippage: Decimal


@dataclass(frozen=True)
class State:
    config: CoreConfig
    status: BotStatus = BotStatus.STARTING
    started_at_ms: int = 0
    markets: dict[str, MarketInfo] = field(default_factory=dict)
    books: dict[str, Book] = field(default_factory=dict)
    positions: dict[str, Position] = field(default_factory=dict)
    collateral_usdc: Decimal | None = None
    triggers: dict[int, TriggerOrder] = field(default_factory=dict)
    pending_orders: dict[str, PendingOrder] = field(default_factory=dict)  # at most one per asset
    subscribed_assets: frozenset[str] = frozenset()
    stale_assets: frozenset[str] = frozenset()
    probe_sent_at_ms: dict[str, int] = field(default_factory=dict)
    last_heartbeat_ms: int = 0
    positions_fetch_attempts: int = 0
    next_trigger_id: int = 1
    next_request_id: int = 1


# EVENTS


@dataclass(frozen=True)
class Started:
    at_ms: int


@dataclass(frozen=True)
class Ticked:
    at_ms: int


@dataclass(frozen=True)
class GeoblockChecked:
    at_ms: int
    blocked: bool
    country: str
    error: str | None = None  # set = request failed


@dataclass(frozen=True)
class PositionsLoaded:
    at_ms: int
    positions: tuple[Position, ...]
    markets: tuple[MarketInfo, ...]
    books: tuple[tuple[str, Book], ...]
    collateral_usdc: Decimal
    reply_chat_id: int | None = None


@dataclass(frozen=True)
class PositionsFetchFailed:
    at_ms: int
    error: str
    reply_chat_id: int | None = None


@dataclass(frozen=True)
class BookUpdated:
    at_ms: int
    asset_id: str
    book: Book


@dataclass(frozen=True)
class BookFetchFailed:
    at_ms: int
    asset_id: str
    error: str


@dataclass(frozen=True)
class TickSizeChanged:
    at_ms: int
    asset_id: str
    tick_size: Decimal


@dataclass(frozen=True)
class MarketResolved:
    at_ms: int
    asset_ids: tuple[str, ...]
    winning_asset_id: str | None


@dataclass(frozen=True)
class OrderFilled:
    at_ms: int
    request_id: str
    filled_shares: Decimal
    filled_usdc: Decimal
    order_id: str
    trade_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class OrderDelayed:
    at_ms: int
    request_id: str
    order_id: str


@dataclass(frozen=True)
class OrderRejected:
    at_ms: int
    request_id: str
    code: str


@dataclass(frozen=True)
class OrderOutcomeUnknown:
    at_ms: int
    request_id: str
    error: str


@dataclass(frozen=True)
class OrderPolled:
    at_ms: int
    request_id: str
    filled_shares: Decimal
    filled_usdc: Decimal
    is_final: bool


@dataclass(frozen=True)
class FillsReconciled:
    at_ms: int
    request_id: str
    filled_shares: Decimal
    filled_usdc: Decimal
    trade_ids: tuple[str, ...]


@dataclass(frozen=True)
class CheckFailed:
    at_ms: int
    request_id: str
    error: str


@dataclass(frozen=True)
class BalanceLoaded:
    at_ms: int
    asset_id: str
    size: Decimal


@dataclass(frozen=True)
class BalanceFetchFailed:
    at_ms: int
    asset_id: str
    error: str


@dataclass(frozen=True)
class UserRequestReceived:
    at_ms: int
    chat_id: int
    request: UserRequest
    market: MarketInfo | None = None
    book: Book | None = None


@dataclass(frozen=True)
class FatalErrorOccurred:
    at_ms: int
    reason: str


Event = (
    Started
    | Ticked
    | GeoblockChecked
    | PositionsLoaded
    | PositionsFetchFailed
    | BookUpdated
    | BookFetchFailed
    | TickSizeChanged
    | MarketResolved
    | OrderFilled
    | OrderDelayed
    | OrderRejected
    | OrderOutcomeUnknown
    | OrderPolled
    | FillsReconciled
    | CheckFailed
    | BalanceLoaded
    | BalanceFetchFailed
    | UserRequestReceived
    | FatalErrorOccurred
)


# COMMANDS


@dataclass(frozen=True)
class Notify:
    chat_id: int | None  # None = broadcast
    text: str


@dataclass(frozen=True)
class CheckGeoblock:
    pass


@dataclass(frozen=True)
class FetchPositions:
    reply_chat_id: int | None


@dataclass(frozen=True)
class FetchBook:
    asset_id: str


@dataclass(frozen=True)
class FetchBalance:
    asset_id: str


@dataclass(frozen=True)
class Subscribe:
    asset_ids: tuple[str, ...]


@dataclass(frozen=True)
class SendSellOrder:
    request_id: str
    asset_id: str
    shares: Decimal
    floor_price: Decimal


@dataclass(frozen=True)
class SendBuyOrder:
    request_id: str
    asset_id: str
    amount_usdc: Decimal
    max_price: Decimal


@dataclass(frozen=True)
class PollOrder:
    request_id: str
    order_id: str


@dataclass(frozen=True)
class ReconcileFills:
    request_id: str
    asset_id: str
    side: Literal["BUY", "SELL"]
    since_ms: int
    exclude_trade_ids: frozenset[str]


@dataclass(frozen=True)
class Exit:
    code: int


Command = (
    Notify
    | CheckGeoblock
    | FetchPositions
    | FetchBook
    | FetchBalance
    | Subscribe
    | SendSellOrder
    | SendBuyOrder
    | PollOrder
    | ReconcileFills
    | Exit
)


# VALUATION


@dataclass(frozen=True)
class Valuation:
    mark: Decimal
    value: Decimal
    cost: Decimal
    pnl: Decimal
    pnl_pct: Decimal


def value_position(position: Position, book: Book | None) -> Valuation:
    mark = book.best_bid if book is not None and book.best_bid is not None else Decimal(0)
    value = position.size * mark
    cost = position.size * position.avg_price
    pnl = value - cost
    pnl_pct = pnl / cost * 100 if cost else Decimal(0)
    return Valuation(mark=mark, value=value, cost=cost, pnl=pnl, pnl_pct=pnl_pct)


TERMINAL_TRIGGER_STATUSES = frozenset({TriggerStatus.FILLED, TriggerStatus.FAILED, TriggerStatus.CANCELLED})


def is_active(trigger: TriggerOrder) -> bool:
    # A PARTIAL closed as dust keeps its status but carries a close_reason.
    return trigger.status not in TERMINAL_TRIGGER_STATUSES and trigger.close_reason is None


def remaining_shares(trigger: TriggerOrder, position_size: Decimal) -> Decimal:
    if trigger.size is None:
        return position_size
    return min(trigger.size - trigger.filled_shares, position_size)


def round_up_to_tick(price: Decimal, tick_size: Decimal) -> Decimal:
    return (price / tick_size).to_integral_value(rounding=ROUND_CEILING) * tick_size


def round_down_to_tick(price: Decimal, tick_size: Decimal) -> Decimal:
    return (price / tick_size).to_integral_value(rounding=ROUND_FLOOR) * tick_size


# src.format imports the types above at runtime, so it is imported only once they exist.
from src import format as fmt  # noqa: E402


# STEP


def step(state: State, event: Event) -> tuple[State, list[Command]]:
    raise NotImplementedError
