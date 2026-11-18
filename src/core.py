from dataclasses import dataclass, field, replace
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from enum import StrEnum
from typing import Literal

from constants import (
    DELAYED_ORDER_POLL_SECONDS,
    PENDING_ORDER_ALERT_SECONDS,
    POSITIONS_FETCH_MAX_ATTEMPTS,
    RECONCILE_LOOKBACK_SECONDS,
)
from src.parse import (
    CancelTrigger,
    ClosePosition,
    EditTrigger,
    OpenPosition,
    PlanTrade,
    SetStopLoss,
    SetTakeProfit,
    ShowHealth,
    ShowHelp,
    ShowOrders,
    ShowPositions,
    ShowPrice,
    StopBot,
    UserRequest,
)


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
    taker_fee_rate: Decimal = Decimal(0)
    fee_exponent: Decimal = Decimal(1)


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
    stuck_notified: bool = False


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
    positions_retry_due: bool = False
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
    monospace: bool = False  # one Telegram pre block, so tables align


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


@dataclass(frozen=True)
class RiskReward:
    risk: Decimal  # a positive amount
    benefit: Decimal
    risk_pct: Decimal
    benefit_pct: Decimal
    ratio: Decimal | None  # None when benefit <= 0
    break_even_pct: Decimal | None


@dataclass(frozen=True)
class TradePlan:
    shares: Decimal
    gross: RiskReward
    net: RiskReward
    entry_fee: Decimal
    stop_loss_fee: Decimal
    take_profit_fee: Decimal


def plan_trade(request: PlanTrade, market: MarketInfo) -> TradePlan:
    shares = request.amount_usdc / request.entry_price

    def taker_fee(price: Decimal) -> Decimal:
        return shares * market.taker_fee_rate * (price * (1 - price)) ** market.fee_exponent

    entry_fee = taker_fee(request.entry_price)
    stop_loss_fee = taker_fee(request.stop_loss_price)
    take_profit_fee = taker_fee(request.take_profit_price)
    gross_risk = shares * (request.entry_price - request.stop_loss_price)
    gross_benefit = shares * (request.take_profit_price - request.entry_price)
    return TradePlan(
        shares=shares,
        gross=_risk_reward(gross_risk, gross_benefit, request.amount_usdc),
        net=_risk_reward(
            gross_risk + entry_fee + stop_loss_fee, gross_benefit - entry_fee - take_profit_fee, request.amount_usdc
        ),
        entry_fee=entry_fee,
        stop_loss_fee=stop_loss_fee,
        take_profit_fee=take_profit_fee,
    )


def _risk_reward(risk: Decimal, benefit: Decimal, stake: Decimal) -> RiskReward:
    has_edge = benefit > 0
    return RiskReward(
        risk=risk,
        benefit=benefit,
        risk_pct=risk / stake * 100,
        benefit_pct=benefit / stake * 100,
        ratio=benefit / risk if has_edge else None,
        break_even_pct=risk / (risk + benefit) * 100 if has_edge else None,
    )


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


Result = tuple[State, list[Command]]


def step(state: State, event: Event) -> Result:
    if state.status == BotStatus.HALTED:
        return state, []
    match event:
        case Started(at_ms=at_ms):
            started = replace(state, status=BotStatus.GEOBLOCK_CHECK, started_at_ms=at_ms, last_heartbeat_ms=at_ms)
            return started, [CheckGeoblock()]
        case GeoblockChecked():
            return _on_geoblock_checked(state, event)
        case PositionsLoaded():
            return _on_positions_loaded(state, event)
        case PositionsFetchFailed():
            return _on_positions_fetch_failed(state, event)
        case Ticked(at_ms=at_ms):
            return _on_ticked(state, at_ms)
        case BookUpdated():
            return _on_book_updated(state, event)
        case TickSizeChanged(asset_id=asset_id, tick_size=tick_size) if asset_id in state.markets:
            market = replace(state.markets[asset_id], tick_size=tick_size)
            return replace(state, markets={**state.markets, asset_id: market}), []
        case OrderFilled() | FillsReconciled():
            return _settle_order(state, event.request_id, event.filled_shares, event.filled_usdc, event.trade_ids)
        case OrderPolled(is_final=True):
            return _settle_order(state, event.request_id, event.filled_shares, event.filled_usdc, ())
        case OrderDelayed(request_id=request_id, order_id=order_id, at_ms=at_ms):
            return _update_pending(state, request_id, order_id=order_id, last_check_ms=at_ms), []
        case OrderOutcomeUnknown(request_id=request_id, at_ms=at_ms) if request_id in state.pending_orders:
            state = _update_pending(state, request_id, outcome_unknown=True, last_check_ms=at_ms)
            return state, [_reconcile_command(state, state.pending_orders[request_id])]
        case CheckFailed(request_id=request_id):
            # last_check_ms 0 makes the next Ticked re-check.
            return _update_pending(state, request_id, last_check_ms=0), []
        case OrderRejected():
            return _on_order_rejected(state, event)
        case BalanceLoaded():
            return _on_balance_loaded(state, event)
        case MarketResolved():
            return _on_market_resolved(state, event)
        case UserRequestReceived():
            return _on_user_request(state, event)
        case FatalErrorOccurred(reason=reason):
            return replace(state, status=BotStatus.HALTED), [
                Notify(None, f"{fmt.EMOJI_HALTED} Halted: {reason}"),
                Exit(2),
            ]
        case _:
            return state, []


def _on_geoblock_checked(state: State, event: GeoblockChecked) -> Result:
    if event.error is not None:
        return state, [Notify(None, f"{fmt.EMOJI_GAP_ALERT} Geoblock check failed: {event.error} — exiting"), Exit(1)]
    if event.blocked:
        return replace(state, status=BotStatus.HALTED), [
            Notify(None, f"{fmt.EMOJI_GAP_ALERT} Geoblocked ({event.country}) — exiting"),
            Exit(2),
        ]
    return replace(state, status=BotStatus.RECONCILING), [FetchPositions(None)]


def _on_positions_fetch_failed(state: State, event: PositionsFetchFailed) -> Result:
    if state.status != BotStatus.RECONCILING:
        return state, [Notify(event.reply_chat_id, f"{fmt.EMOJI_FAILED} Positions fetch failed: {event.error}")]
    attempts = state.positions_fetch_attempts + 1
    state = replace(state, positions_fetch_attempts=attempts)
    if attempts < POSITIONS_FETCH_MAX_ATTEMPTS:
        return replace(state, positions_retry_due=True), []
    return state, [
        Notify(None, f"{fmt.EMOJI_FAILED} Positions fetch failed {attempts} times: {event.error} — exiting"),
        Exit(1),
    ]


def _on_positions_loaded(state: State, event: PositionsLoaded) -> Result:
    # Merged, never replaced: the positions API can lag fresh fills; BalanceLoaded is what drops a position.
    positions = {
        **state.positions,
        **{position.asset_id: position for position in event.positions if position.size > 0},
    }
    books = state.books
    for asset_id, book in event.books:
        books = _merge_book(books, asset_id, book)
    new_assets = tuple(asset_id for asset_id in positions if asset_id not in state.subscribed_assets)
    state = replace(
        state,
        positions=positions,
        markets={**state.markets, **{market.asset_id: market for market in event.markets}},
        books=books,
        collateral_usdc=event.collateral_usdc,
        subscribed_assets=state.subscribed_assets | set(new_assets),
    )
    commands: list[Command] = [Subscribe(new_assets)] if new_assets else []
    if state.status != BotStatus.RECONCILING:
        return state, [*commands, Notify(event.reply_chat_id, fmt.render_positions(state, event.at_ms))]
    unprotected = [
        f"• {_label(state, asset_id)} ({position.size} shares)"
        for asset_id, position in state.positions.items()
        if not _triggers_on(state, asset_id, ACTIVE_STATUSES)
    ]
    started_text = "\n".join(
        [
            f"{fmt.EMOJI_STARTED} Bot started — trigger orders are not persisted",
            f"Unprotected positions: {len(unprotected) or 'none'}",
            *unprotected,
        ]
    )
    return replace(state, status=BotStatus.RUNNING), [*commands, Notify(None, started_text)]


def _on_ticked(state: State, at_ms: int) -> Result:
    if state.status == BotStatus.RECONCILING and state.positions_retry_due:
        return replace(state, positions_retry_due=False), [FetchPositions(None)]
    if state.status not in LIVE_STATUSES:
        return state, []
    state, stale_commands = _check_staleness(state, at_ms)
    state, check_commands = _check_pending_orders(state, at_ms)
    heartbeat_ms = state.config.heartbeat_ms
    if heartbeat_ms <= 0 or at_ms - state.last_heartbeat_ms < heartbeat_ms:
        return state, stale_commands + check_commands
    heartbeat = Notify(None, f"{fmt.EMOJI_HEARTBEAT} {fmt.render_health(state, at_ms)}")
    return replace(state, last_heartbeat_ms=at_ms), [*stale_commands, *check_commands, heartbeat]


def _check_staleness(state: State, at_ms: int) -> Result:
    half_stale_ms = state.config.stale_ms // 2
    watched_assets = list(dict.fromkeys(trigger.asset_id for trigger in state.triggers.values() if is_active(trigger)))
    probe_sent_at_ms = dict(state.probe_sent_at_ms)
    commands: list[Command] = []
    newly_stale: list[str] = []
    for asset_id in watched_assets:
        book = state.books.get(asset_id)
        age_ms = None if book is None else at_ms - book.received_at_ms
        last_probe_ms = probe_sent_at_ms.get(asset_id)
        if (age_ms is None or age_ms > half_stale_ms) and (
            last_probe_ms is None or at_ms - last_probe_ms >= half_stale_ms
        ):
            commands.append(FetchBook(asset_id))
            probe_sent_at_ms[asset_id] = at_ms
        if (age_ms is None or age_ms > state.config.stale_ms) and asset_id not in state.stale_assets:
            newly_stale.append(asset_id)
    stale_assets = (state.stale_assets & set(watched_assets)) | set(newly_stale)
    state = replace(state, probe_sent_at_ms=probe_sent_at_ms, stale_assets=stale_assets)
    if stale_assets and state.status == BotStatus.RUNNING:
        labels = ", ".join(_label(state, asset_id) for asset_id in newly_stale)
        return replace(state, status=BotStatus.STALE), [
            *commands,
            Notify(None, f"{fmt.EMOJI_STALE} Stale data: {labels}"),
        ]
    if not stale_assets and state.status == BotStatus.STALE:
        return replace(state, status=BotStatus.RUNNING), [
            *commands,
            Notify(None, f"{fmt.EMOJI_FRESH} Data fresh again"),
        ]
    return state, commands


def _check_pending_orders(state: State, at_ms: int) -> Result:
    poll_ms = DELAYED_ORDER_POLL_SECONDS * 1000
    commands: list[Command] = []
    pending_orders = dict(state.pending_orders)
    for request_id, pending in state.pending_orders.items():
        if not pending.stuck_notified and at_ms - pending.sent_at_ms >= PENDING_ORDER_ALERT_SECONDS * 1000:
            pending = pending_orders[request_id] = replace(pending, stuck_notified=True)
            text = (
                f"{fmt.EMOJI_GAP_ALERT} {_order_subject(state, pending)}: order {request_id} unresolved after "
                f"{PENDING_ORDER_ALERT_SECONDS}s, still checking"
            )
            commands.append(Notify(pending.reply_chat_id, text))
        if pending.order_id is not None and at_ms - pending.last_check_ms >= poll_ms:
            commands.append(PollOrder(request_id, pending.order_id))
        elif pending.outcome_unknown and pending.last_check_ms == 0:
            commands.append(_reconcile_command(state, pending))
        else:
            continue
        pending_orders[request_id] = replace(pending, last_check_ms=at_ms)
    return replace(state, pending_orders=pending_orders), commands


def _on_book_updated(state: State, event: BookUpdated) -> Result:
    asset_id, book = event.asset_id, event.book
    was_stale = asset_id in state.stale_assets
    state = replace(
        state,
        books={**state.books, asset_id: book},
        probe_sent_at_ms={key: value for key, value in state.probe_sent_at_ms.items() if key != asset_id},
        stale_assets=state.stale_assets - {asset_id},
    )
    commands: list[Command] = []
    if was_stale and not state.stale_assets and state.status == BotStatus.STALE:
        state = replace(state, status=BotStatus.RUNNING)
        commands.append(Notify(None, f"{fmt.EMOJI_FRESH} Data fresh again"))
    if book.best_bid is not None:
        label = _label(state, asset_id)
        for trigger in _triggers_on(state, asset_id, frozenset({TriggerStatus.ARMED})):
            if not _is_through(trigger.kind, trigger.trigger_price, book.best_bid):
                continue
            if was_stale and trigger.policy == GapPolicy.ALERT:
                state = _put_trigger(state, _cancelled(trigger, "gap_alert"))
                text = (
                    f"{fmt.EMOJI_GAP_ALERT} {_name(trigger)} {label} gapped: bid {book.best_bid} vs trigger "
                    f"{trigger.trigger_price} — not selling. /close or re-arm"
                )
            else:
                state = _put_trigger(state, replace(trigger, status=TriggerStatus.TRIGGERED))
                text = (
                    f"{fmt.EMOJI_TRIGGERED} {_name(trigger)} {label} triggered: "
                    f"bid {book.best_bid} vs trigger {trigger.trigger_price}"
                )
            commands.append(Notify(None, text))
    state, sell_commands = _fire_triggers(state, asset_id, event.at_ms)
    return state, commands + sell_commands


def _fire_triggers(state: State, asset_id: str, at_ms: int) -> Result:
    best_bid = state.books[asset_id].best_bid
    if best_bid is None:
        return state, []
    commands: list[Command] = []
    for trigger in _triggers_on(state, asset_id, FIRING_STATUSES):
        if _has_pending_order(state, asset_id):
            break
        market = state.markets[asset_id]
        subject = f"{_name(trigger)} {_label(state, asset_id)}"
        shares = remaining_shares(trigger, _position_size(state, asset_id))
        if shares < market.min_order_size:
            below_min = f"below min order size {market.min_order_size}"
            if trigger.status == TriggerStatus.PARTIAL:
                closed = replace(trigger, close_reason="dust")
                text = f"{fmt.EMOJI_CANCELLED} {subject}: {shares} shares left, {below_min} — closed as dust"
            else:
                closed = replace(trigger, status=TriggerStatus.FAILED, close_reason="below_min_size")
                text = f"{fmt.EMOJI_FAILED} {subject}: {shares} shares to sell, {below_min} — failed"
            state = _put_trigger(state, closed)
            commands.append(Notify(None, text))
        elif best_bid >= trigger.floor_price:
            state, request_id = _new_request_id(state)
            floor_price = round_up_to_tick(trigger.floor_price, market.tick_size)
            pending = PendingOrder(
                request_id, asset_id, "SELL", sent_at_ms=at_ms, trigger_id=trigger.trigger_id, reply_chat_id=None
            )
            state = _put_pending(state, pending)
            state = _put_trigger(state, replace(trigger, status=TriggerStatus.ORDER_PENDING, waiting_notified=False))
            commands += [
                SendSellOrder(request_id, asset_id, shares, floor_price),
                Notify(None, f"{fmt.EMOJI_PLACED} {subject}: selling {shares} shares, floor {floor_price}"),
            ]
        elif not trigger.waiting_notified:
            state = _put_trigger(state, replace(trigger, waiting_notified=True))
            text = f"{fmt.EMOJI_WAITING} {subject}: bid {best_bid} < floor {trigger.floor_price}, waiting"
            commands.append(Notify(None, text))
    return state, commands


def _settle_order(
    state: State, request_id: str, filled_shares: Decimal, filled_usdc: Decimal, trade_ids: tuple[str, ...]
) -> Result:
    pending = state.pending_orders.get(request_id)
    if pending is None:
        return state, []
    state = _drop_pending(state, request_id)
    if pending.side == "BUY":
        return _settle_buy(state, pending, filled_shares, filled_usdc)
    asset_id = pending.asset_id
    label = _label(state, asset_id)
    state = _with_position_size(state, asset_id, max(_position_size(state, asset_id) - filled_shares, Decimal(0)))
    if state.collateral_usdc is not None:
        state = replace(state, collateral_usdc=state.collateral_usdc + filled_usdc)
    fetch_balance = FetchBalance(asset_id)
    fill_text = f"sold {filled_shares} @ {_average_price(filled_shares, filled_usdc)} for {filled_usdc} USDC"
    if pending.trigger_id is None:
        if filled_shares == 0:
            return state, [
                Notify(pending.reply_chat_id, f"{fmt.EMOJI_FAILED} /close {label} not filled"),
                fetch_balance,
            ]
        return state, [Notify(pending.reply_chat_id, f"{fmt.EMOJI_FILLED} /close {label}: {fill_text}"), fetch_balance]
    trigger = state.triggers[pending.trigger_id]
    if filled_shares == 0:
        return _put_trigger(state, replace(trigger, status=_firing_status(trigger))), [fetch_balance]
    trigger = replace(
        trigger,
        filled_shares=trigger.filled_shares + filled_shares,
        proceeds_usdc=trigger.proceeds_usdc + filled_usdc,
        credited_trade_ids=trigger.credited_trade_ids | set(trade_ids),
    )
    is_done = remaining_shares(trigger, _position_size(state, asset_id)) <= 0
    trigger = replace(trigger, status=TriggerStatus.FILLED if is_done else TriggerStatus.PARTIAL)
    emoji = fmt.EMOJI_FILLED if is_done else fmt.EMOJI_PARTIAL
    return _put_trigger(state, trigger), [Notify(None, f"{emoji} {_name(trigger)} {label}: {fill_text}"), fetch_balance]


def _settle_buy(state: State, pending: PendingOrder, filled_shares: Decimal, filled_usdc: Decimal) -> Result:
    label = _label(state, pending.asset_id)
    if filled_shares == 0:
        return state, [Notify(pending.reply_chat_id, f"{fmt.EMOJI_FAILED} /open {label} not filled")]
    position = state.positions.get(pending.asset_id)
    held, cost = (position.size, position.size * position.avg_price) if position else (Decimal(0), Decimal(0))
    new_size = held + filled_shares
    state = _with_position_size(state, pending.asset_id, new_size, (cost + filled_usdc) / new_size)
    if state.collateral_usdc is not None:
        state = replace(state, collateral_usdc=state.collateral_usdc - filled_usdc)
    fill_text = f"bought {filled_shares} @ {_average_price(filled_shares, filled_usdc)} for {filled_usdc} USDC"
    return state, [Notify(pending.reply_chat_id, f"{fmt.EMOJI_FILLED} /open {label}: {fill_text}")]


def _on_order_rejected(state: State, event: OrderRejected) -> Result:
    pending = state.pending_orders.get(event.request_id)
    if pending is None:
        return state, []
    state = _drop_pending(state, event.request_id)
    subject = _order_subject(state, pending)
    commands: list[Command] = [FetchBalance(pending.asset_id)] if event.code == "not_enough_balance" else []
    if pending.trigger_id is None:
        return state, [
            Notify(pending.reply_chat_id, f"{fmt.EMOJI_FAILED} {subject} rejected: {event.code}"),
            *commands,
        ]
    trigger = state.triggers[pending.trigger_id]
    if event.code in ("fak_not_filled", "not_enough_balance"):
        retrying = Notify(None, f"{fmt.EMOJI_FAILED} {subject}: order rejected ({event.code}) — retrying")
        return _put_trigger(state, replace(trigger, status=_firing_status(trigger))), [retrying, *commands]
    failed = replace(trigger, status=TriggerStatus.FAILED, close_reason=event.code)
    return _put_trigger(state, failed), [
        Notify(None, f"{fmt.EMOJI_FAILED} {subject}: order rejected ({event.code}) — trigger failed")
    ]


def _on_balance_loaded(state: State, event: BalanceLoaded) -> Result:
    asset_id, size = event.asset_id, event.size
    state = _with_position_size(state, asset_id, size)
    commands: list[Command] = []
    for trigger in _triggers_on(state, asset_id, CANCELLABLE_STATUSES):
        subject = f"{_name(trigger)} {_label(state, asset_id)}"
        if size < state.markets[asset_id].min_order_size:
            state = _put_trigger(state, _cancelled(trigger, "position_closed"))
            text = f"{fmt.EMOJI_CANCELLED} {subject} cancelled: position {size} is below min order size"
            commands.append(Notify(None, text))
        elif trigger.size is not None and trigger.size - trigger.filled_shares > size:
            new_size = trigger.filled_shares + size
            state = _put_trigger(state, replace(trigger, size=new_size))
            text = f"{fmt.EMOJI_RESIZED} {subject} resized to {new_size} shares (position {size})"
            commands.append(Notify(None, text))
    return state, commands


def _on_market_resolved(state: State, event: MarketResolved) -> Result:
    commands: list[Command] = []
    for asset_id in event.asset_ids:
        if event.winning_asset_id is None:
            verdict = "no winner"
        else:
            verdict = "won" if asset_id == event.winning_asset_id else "lost"
        outcome = f"market resolved — {_label(state, asset_id)} {verdict}"
        for trigger in _triggers_on(state, asset_id, CANCELLABLE_STATUSES):
            state = _put_trigger(state, _cancelled(trigger, "market_resolved"))
            commands.append(Notify(None, f"{fmt.EMOJI_CANCELLED} {_name(trigger)} cancelled: {outcome}"))
    return state, commands


def _on_user_request(state: State, event: UserRequestReceived) -> Result:
    request, chat_id = event.request, event.chat_id
    if isinstance(request, ShowHealth):
        return state, [Notify(chat_id, fmt.render_health(state, event.at_ms))]
    if isinstance(request, ShowHelp):
        return state, [Notify(chat_id, fmt.render_help(state.config.close_slippage))]
    if isinstance(request, StopBot):
        return _stop_bot(state, chat_id)
    if state.status not in LIVE_STATUSES:
        return state, [Notify(chat_id, f"{fmt.EMOJI_FAILED} Bot is starting, retry in a moment")]
    subscribe_commands: list[Command] = []
    if event.market is not None:
        state, subscribe_commands = _cache_market(state, event.market, event.book)
    match request:
        case ShowOrders():
            commands: list[Command] = [Notify(chat_id, fmt.render_orders(state))]
        case ShowPositions():
            commands = [FetchPositions(chat_id)]
        case ShowPrice():
            commands = [Notify(chat_id, fmt.render_price(event.market, state.books[event.market.asset_id]))]
        case PlanTrade():
            market = event.market
            if _off_tick(market.tick_size, request.entry_price, request.stop_loss_price, request.take_profit_price):
                text = f"{fmt.EMOJI_FAILED} prices must be multiples of the tick size {market.tick_size}"
                commands = [Notify(chat_id, text)]
            else:
                text = fmt.render_plan(market, state.books[market.asset_id], request, plan_trade(request, market))
                commands = [Notify(chat_id, text, monospace=True)]
        case SetStopLoss():
            trigger = TriggerOrder(
                trigger_id=state.next_trigger_id,
                kind=TriggerKind.STOP_LOSS,
                asset_id=event.market.asset_id,
                trigger_price=request.trigger_price,
                floor_price=request.floor_price,
                size=request.size,
                policy=GapPolicy(request.policy or "sell"),
            )
            state, commands = _arm_trigger(state, chat_id, trigger)
        case SetTakeProfit():
            trigger = TriggerOrder(
                trigger_id=state.next_trigger_id,
                kind=TriggerKind.TAKE_PROFIT,
                asset_id=event.market.asset_id,
                trigger_price=request.trigger_price,
                floor_price=request.trigger_price if request.floor_price is None else request.floor_price,
                size=request.size,
                policy=GapPolicy.SELL,
            )
            state, commands = _arm_trigger(state, chat_id, trigger)
        case EditTrigger():
            state, commands = _edit_trigger(state, chat_id, request)
        case CancelTrigger():
            state, commands = _cancel_trigger(state, chat_id, request.trigger_id)
        case OpenPosition() | ClosePosition() if _has_pending_order(state, event.market.asset_id):
            label = _label(state, event.market.asset_id)
            commands = [
                Notify(chat_id, f"{fmt.EMOJI_FAILED} an order is already in flight on {label}, retry in a moment")
            ]
        case OpenPosition():
            state, commands = _open_position(state, event, request)
        case ClosePosition():
            state, commands = _close_position(state, event, request)
    return state, subscribe_commands + commands


def _stop_bot(state: State, chat_id: int) -> Result:
    # Exiting mid-order would lose its outcome.
    if state.pending_orders:
        return state, [Notify(chat_id, f"{fmt.EMOJI_FAILED} an order is in flight, retry /exit in a moment")]
    dropped = [
        f"• {fmt.format_trigger(trigger)} {_label(state, trigger.asset_id)}"
        for trigger in state.triggers.values()
        if is_active(trigger)
    ]
    text = "\n".join([f"{fmt.EMOJI_EXIT} Exiting on /exit — trigger orders dropped: {len(dropped)}", *dropped])
    return state, [Notify(None, text), Exit(0)]


def _cache_market(state: State, market: MarketInfo, book: Book | None) -> Result:
    asset_id = market.asset_id
    books = state.books if book is None else _merge_book(state.books, asset_id, book)
    state = replace(state, markets={**state.markets, asset_id: market}, books=books)
    if asset_id in state.subscribed_assets:
        return state, []
    return replace(state, subscribed_assets=state.subscribed_assets | {asset_id}), [Subscribe((asset_id,))]


def _arm_trigger(state: State, chat_id: int, trigger: TriggerOrder) -> Result:
    error = _trigger_error(state, trigger)
    if error is not None:
        return state, [Notify(chat_id, f"{fmt.EMOJI_FAILED} {error}")]
    state = replace(_put_trigger(state, trigger), next_trigger_id=state.next_trigger_id + 1)
    return state, [Notify(chat_id, f"{fmt.EMOJI_ARMED} {_name(trigger)} armed — {_terms(state, trigger)}")]


def _edit_trigger(state: State, chat_id: int, request: EditTrigger) -> Result:
    trigger = state.triggers.get(request.trigger_id)
    if trigger is None:
        return state, [Notify(chat_id, f"{fmt.EMOJI_FAILED} #{request.trigger_id} not found")]
    if trigger.status != TriggerStatus.ARMED:
        return state, [
            Notify(
                chat_id,
                f"{fmt.EMOJI_FAILED} #{trigger.trigger_id} is {trigger.status}, only ARMED triggers can be edited",
            )
        ]
    if request.floor_price is not None:
        floor_price = request.floor_price
    elif trigger.kind == TriggerKind.TAKE_PROFIT:
        floor_price = request.trigger_price
    else:
        floor_price = trigger.floor_price
    edited = replace(trigger, trigger_price=request.trigger_price, floor_price=floor_price)
    error = _trigger_error(state, edited)
    if error is not None:
        return state, [Notify(chat_id, f"{fmt.EMOJI_FAILED} {error}")]
    return _put_trigger(state, edited), [
        Notify(chat_id, f"{fmt.EMOJI_ARMED} {_name(edited)} updated — {_terms(state, edited)}")
    ]


def _cancel_trigger(state: State, chat_id: int, trigger_id: int) -> Result:
    trigger = state.triggers.get(trigger_id)
    if trigger is None:
        return state, [Notify(chat_id, f"{fmt.EMOJI_FAILED} #{trigger_id} not found")]
    if trigger.status == TriggerStatus.ORDER_PENDING:
        return state, [Notify(chat_id, f"{fmt.EMOJI_FAILED} #{trigger_id} has an order in flight, retry in a moment")]
    if trigger.status not in CANCELLABLE_STATUSES or not is_active(trigger):
        return state, [Notify(chat_id, f"{fmt.EMOJI_FAILED} #{trigger_id} is already closed ({trigger.status})")]
    cancelled = _cancelled(trigger, "user")
    label = _label(state, trigger.asset_id)
    return _put_trigger(state, cancelled), [
        Notify(chat_id, f"{fmt.EMOJI_CANCELLED} {_name(trigger)} {label} cancelled")
    ]


def _open_position(state: State, event: UserRequestReceived, request: OpenPosition) -> Result:
    market = event.market
    max_price = round_down_to_tick(request.max_price, market.tick_size)
    label = _label(state, market.asset_id)
    # A market BUY sizes as usdc / max_price shares, which the CLOB checks against the book's min_order_size.
    min_usdc = market.min_order_size * max_price
    if request.amount_usdc < min_usdc:
        text = (
            f"{fmt.EMOJI_FAILED} /open {label}: min order size is {market.min_order_size} shares, "
            f"at max price {max_price} that needs at least {min_usdc} USDC"
        )
        return state, [Notify(event.chat_id, text)]
    state, request_id = _new_request_id(state)
    pending = PendingOrder(
        request_id, market.asset_id, "BUY", sent_at_ms=event.at_ms, trigger_id=None, reply_chat_id=event.chat_id
    )
    return _put_pending(state, pending), [
        SendBuyOrder(request_id, market.asset_id, request.amount_usdc, max_price),
        Notify(
            event.chat_id, f"{fmt.EMOJI_PLACED} /open {label}: buying {request.amount_usdc} USDC, max price {max_price}"
        ),
    ]


def _close_position(state: State, event: UserRequestReceived, request: ClosePosition) -> Result:
    asset_id, tick_size = event.market.asset_id, event.market.tick_size
    label = _label(state, asset_id)
    best_bid = state.books[asset_id].best_bid if asset_id in state.books else None
    shares = request.shares if request.shares is not None else _position_size(state, asset_id)
    if best_bid is None:
        return state, [Notify(event.chat_id, f"{fmt.EMOJI_FAILED} no bid on {label}")]
    if shares <= 0:
        return state, [Notify(event.chat_id, f"{fmt.EMOJI_FAILED} no position on {label}")]
    if request.floor_price is not None:
        floor_price = round_up_to_tick(request.floor_price, tick_size)
    else:
        floor_price = max(tick_size, round_up_to_tick(best_bid - state.config.close_slippage, tick_size))
    state, request_id = _new_request_id(state)
    pending = PendingOrder(
        request_id, asset_id, "SELL", sent_at_ms=event.at_ms, trigger_id=None, reply_chat_id=event.chat_id
    )
    return _put_pending(state, pending), [
        SendSellOrder(request_id, asset_id, shares, floor_price),
        Notify(event.chat_id, f"{fmt.EMOJI_PLACED} /close {label}: selling {shares} shares, floor {floor_price}"),
    ]


def _trigger_error(state: State, trigger: TriggerOrder) -> str | None:
    market = state.markets[trigger.asset_id]
    tick_size, min_order_size = market.tick_size, market.min_order_size
    position_size = _position_size(state, trigger.asset_id)
    book = state.books.get(trigger.asset_id)
    best_bid = book.best_bid if book is not None else None
    if not 0 < trigger.floor_price <= trigger.trigger_price < 1:
        prices = f"trigger {trigger.trigger_price}, floor {trigger.floor_price}"
        return f"prices must satisfy 0 < floor <= trigger < 1 ({prices})"
    if _off_tick(tick_size, trigger.trigger_price, trigger.floor_price):
        return f"prices must be multiples of the tick size {tick_size}"
    if position_size < min_order_size:
        return f"position {position_size} is below the min order size {min_order_size}"
    if trigger.size is not None and trigger.size < min_order_size:
        return f"size {trigger.size} is below the min order size {min_order_size}"
    if best_bid is not None and _is_through(trigger.kind, trigger.trigger_price, best_bid):
        direction = "below" if trigger.kind == TriggerKind.STOP_LOSS else "above"
        return f"bid {best_bid} is already at or {direction} trigger {trigger.trigger_price}"
    return None


def _terms(state: State, trigger: TriggerOrder) -> str:
    size = "all" if trigger.size is None else trigger.size
    return (
        f"{_label(state, trigger.asset_id)}: trigger {trigger.trigger_price}, floor {trigger.floor_price}, "
        f"size {size}, gap policy {trigger.policy}"
    )


# HELPERS


ACTIVE_STATUSES = frozenset(TriggerStatus) - TERMINAL_TRIGGER_STATUSES
FIRING_STATUSES = frozenset({TriggerStatus.TRIGGERED, TriggerStatus.PARTIAL})
# ORDER_PENDING is excluded: its order resolves first (a rejection re-checks the balance).
CANCELLABLE_STATUSES = frozenset({TriggerStatus.ARMED, *FIRING_STATUSES})
LIVE_STATUSES = frozenset({BotStatus.RUNNING, BotStatus.STALE})


def _name(trigger: TriggerOrder) -> str:
    return f"{trigger.kind} #{trigger.trigger_id}"


def _off_tick(tick_size: Decimal, *prices: Decimal) -> bool:
    return any((price / tick_size) % 1 for price in prices)


def _is_through(kind: TriggerKind, trigger_price: Decimal, best_bid: Decimal) -> bool:
    return best_bid <= trigger_price if kind == TriggerKind.STOP_LOSS else best_bid >= trigger_price


def _cancelled(trigger: TriggerOrder, reason: str) -> TriggerOrder:
    return replace(trigger, status=TriggerStatus.CANCELLED, close_reason=reason)


def _firing_status(trigger: TriggerOrder) -> TriggerStatus:
    return TriggerStatus.PARTIAL if trigger.filled_shares > 0 else TriggerStatus.TRIGGERED


def _average_price(shares: Decimal, usdc: Decimal) -> Decimal:
    return (usdc / shares).quantize(Decimal("0.0001")).normalize() if shares else Decimal(0)


def _position_size(state: State, asset_id: str) -> Decimal:
    position = state.positions.get(asset_id)
    return position.size if position is not None else Decimal(0)


def _with_position_size(state: State, asset_id: str, size: Decimal, avg_price: Decimal | None = None) -> State:
    position = state.positions.get(asset_id)
    if avg_price is None:
        avg_price = position.avg_price if position is not None else Decimal(0)
    positions = dict(state.positions)
    if size > 0:
        positions[asset_id] = Position(asset_id, size, avg_price)
    else:
        positions.pop(asset_id, None)
    return replace(state, positions=positions)


def _has_pending_order(state: State, asset_id: str) -> bool:
    return any(pending.asset_id == asset_id for pending in state.pending_orders.values())


def _new_request_id(state: State) -> tuple[State, str]:
    return replace(state, next_request_id=state.next_request_id + 1), f"r{state.next_request_id}"


def _put_trigger(state: State, trigger: TriggerOrder) -> State:
    return replace(state, triggers={**state.triggers, trigger.trigger_id: trigger})


def _put_pending(state: State, pending: PendingOrder) -> State:
    return replace(state, pending_orders={**state.pending_orders, pending.request_id: pending})


def _drop_pending(state: State, request_id: str) -> State:
    return replace(
        state, pending_orders={key: value for key, value in state.pending_orders.items() if key != request_id}
    )


def _update_pending(state: State, request_id: str, **changes) -> State:
    pending = state.pending_orders.get(request_id)
    return state if pending is None else _put_pending(state, replace(pending, **changes))


def _reconcile_command(state: State, pending: PendingOrder) -> ReconcileFills:
    trigger = state.triggers.get(pending.trigger_id) if pending.trigger_id is not None else None
    return ReconcileFills(
        request_id=pending.request_id,
        asset_id=pending.asset_id,
        side=pending.side,
        since_ms=pending.sent_at_ms - RECONCILE_LOOKBACK_SECONDS * 1000,
        exclude_trade_ids=trigger.credited_trade_ids if trigger is not None else frozenset(),
    )


def _label(state: State, asset_id: str) -> str:
    return fmt.market_label(state.markets.get(asset_id), asset_id)


def _order_subject(state: State, pending: PendingOrder) -> str:
    if pending.trigger_id is not None:
        name = _name(state.triggers[pending.trigger_id])
    else:
        name = "/open" if pending.side == "BUY" else "/close"
    return f"{name} {_label(state, pending.asset_id)}"


def _triggers_on(state: State, asset_id: str, statuses: frozenset[TriggerStatus]) -> list[TriggerOrder]:
    return [
        trigger
        for trigger in sorted(state.triggers.values(), key=lambda trigger: trigger.trigger_id)
        if trigger.asset_id == asset_id and trigger.status in statuses and is_active(trigger)
    ]


def _merge_book(books: dict[str, Book], asset_id: str, book: Book) -> dict[str, Book]:
    current = books.get(asset_id)
    if current is not None and current.received_at_ms > book.received_at_ms:
        return books
    return {**books, asset_id: book}
