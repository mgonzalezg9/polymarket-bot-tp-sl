from dataclasses import replace
from decimal import Decimal

from src.core import (
    Book,
    BotStatus,
    Command,
    CoreConfig,
    Event,
    GapPolicy,
    MarketInfo,
    Position,
    State,
    TriggerKind,
    TriggerOrder,
    step,
)

ASSET_ID = "123"
T0_MS = 1_700_000_000_000
CHAT_ID = 42


def _decimal(value: str | Decimal | None) -> Decimal | None:
    return None if value is None else Decimal(value)


def make_config(stale_ms: int = 30_000, heartbeat_ms: int = 0, close_slippage: str = "0.02") -> CoreConfig:
    return CoreConfig(stale_ms=stale_ms, heartbeat_ms=heartbeat_ms, close_slippage=Decimal(close_slippage))


def make_state(**overrides) -> State:
    return replace(State(config=make_config()), **overrides)


def make_market(
    asset_id: str = ASSET_ID,
    slug: str = "will-x-happen",
    outcome: str = "Yes",
    tick_size: str = "0.01",
    min_order_size: str = "5",
    is_v2: bool = False,
) -> MarketInfo:
    return MarketInfo(
        asset_id=asset_id,
        slug=slug,
        outcome=outcome,
        tick_size=Decimal(tick_size),
        min_order_size=Decimal(min_order_size),
        is_v2=is_v2,
    )


def make_book(
    best_bid: str | None,
    best_ask: str | None = None,
    at_ms: int = T0_MS,
    best_bid_size: str | None = "1000",
    best_ask_size: str | None = "1000",
    last_trade_price: str | None = None,
) -> Book:
    return Book(
        best_bid=_decimal(best_bid),
        best_bid_size=_decimal(best_bid_size) if best_bid is not None else None,
        best_ask=_decimal(best_ask),
        best_ask_size=_decimal(best_ask_size) if best_ask is not None else None,
        last_trade_price=_decimal(last_trade_price),
        received_at_ms=at_ms,
    )


def make_trigger(
    trigger_id: int = 1,
    kind: TriggerKind = TriggerKind.STOP_LOSS,
    asset_id: str = ASSET_ID,
    trigger_price: str = "0.45",
    floor_price: str = "0.40",
    size: str | None = None,
    policy: GapPolicy = GapPolicy.SELL,
    **overrides,
) -> TriggerOrder:
    trigger = TriggerOrder(
        trigger_id=trigger_id,
        kind=kind,
        asset_id=asset_id,
        trigger_price=Decimal(trigger_price),
        floor_price=Decimal(floor_price),
        size=_decimal(size),
        policy=policy,
    )
    return replace(trigger, **overrides)


def running_state_with_position(
    size: str = "100",
    avg_price: str = "0.55",
    best_bid: str | None = "0.52",
    best_ask: str | None = "0.54",
    asset_id: str = ASSET_ID,
    at_ms: int = T0_MS,
    triggers: tuple[TriggerOrder, ...] = (),
    **overrides,
) -> State:
    state = make_state(
        status=BotStatus.RUNNING,
        started_at_ms=at_ms,
        markets={asset_id: make_market(asset_id=asset_id)},
        books={asset_id: make_book(best_bid, best_ask, at_ms=at_ms)},
        positions={asset_id: Position(asset_id=asset_id, size=Decimal(size), avg_price=Decimal(avg_price))},
        collateral_usdc=Decimal("1000"),
        triggers={trigger.trigger_id: trigger for trigger in triggers},
        subscribed_assets=frozenset({asset_id}),
        last_heartbeat_ms=at_ms,
        next_trigger_id=max((trigger.trigger_id for trigger in triggers), default=0) + 1,
    )
    return replace(state, **overrides)


def run_events(state: State, *events: Event) -> tuple[State, list[Command]]:
    all_commands: list[Command] = []
    for event in events:
        state, commands = step(state, event)
        all_commands.extend(commands)
    return state, all_commands
