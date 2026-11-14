from datetime import UTC, datetime, timedelta
from decimal import Decimal

from src.core import (
    Book,
    MarketInfo,
    State,
    TriggerOrder,
    TriggerStatus,
    is_active,
    remaining_shares,
    value_position,
)

EMOJI_STARTED = "🚀"
EMOJI_ARMED = "🎯"
EMOJI_TRIGGERED = "⚡"
EMOJI_PLACED = "📤"
EMOJI_FILLED = "✅"
EMOJI_PARTIAL = "🌓"
EMOJI_WAITING = "⏳"
EMOJI_RESIZED = "🔧"
EMOJI_CANCELLED = "🚫"
EMOJI_FAILED = "❌"
EMOJI_STALE = "🟠"
EMOJI_FRESH = "🟢"
EMOJI_GAP_ALERT = "⚠️"
EMOJI_HALTED = "🛑"
EMOJI_CRASH = "💥"
EMOJI_HEARTBEAT = "💓"
EMOJI_POSITIONS = "💼"
EMOJI_PRICE = "📈"
EMOJI_HEALTH = "🩺"
EMOJI_ORDERS = "📦"
EMOJI_BACKLOG = "🧹"
EMOJI_EXIT = "👋"
EMOJI_HELP = "📖"


def market_label(market: MarketInfo | None, asset_id: str) -> str:
    if market is None or not market.slug:
        return f"…{asset_id[-6:]}"
    return f"{market.slug} {market.outcome}"


def format_shares(value: Decimal) -> str:
    return f"{value.normalize():f}"


def format_price(value: Decimal) -> str:
    return f"{value:f}"


def format_usdc(value: Decimal) -> str:
    return f"{value:.2f}"


def format_duration(milliseconds: int) -> str:
    seconds = milliseconds // 1000
    return f"{seconds}s" if seconds < 60 else str(timedelta(seconds=seconds))


def format_timestamp(milliseconds: int) -> str:
    return datetime.fromtimestamp(milliseconds // 1000, UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def format_distance(best_bid: Decimal | None, trigger_price: Decimal) -> str:
    if not best_bid:
        return "n/a"
    return f"{(best_bid - trigger_price) / best_bid * 100:+.2f}%"


def format_trigger_size(trigger: TriggerOrder) -> str:
    return "all" if trigger.size is None else format_shares(trigger.size)


def format_trigger(trigger: TriggerOrder) -> str:
    return (
        f"#{trigger.trigger_id} {trigger.kind} "
        f"{format_price(trigger.trigger_price)}/{format_price(trigger.floor_price)} {format_trigger_size(trigger)}"
    )


def _active_triggers(state: State) -> list[TriggerOrder]:
    return [trigger for trigger in state.triggers.values() if is_active(trigger)]


def render_help(close_slippage: Decimal) -> str:
    return f"""{EMOJI_HELP} Help
<market> = asset id, Polymarket URL, or <slug> <outcome>
Optional values are positional: to set shares on /tp, pass the floor too.

/sl <market> <trigger> <floor> [shares] [sell|alert]
• /sl will-trump-win-2028 yes 0.40 0.35
  When the bid drops to 0.40 or lower, sells everything at a floor of 0.35.
• /sl will-trump-win-2028 yes 0.40 0.35 50 alert
  Covers 50 shares. With alert, a price gap only notifies you and does not sell.

/tp <market> <trigger> [floor] [shares]
• /tp will-trump-win-2028 yes 0.80
  The floor defaults to the trigger.
• /tp https://polymarket.com/event/… 0.80 0.78 25
  Sets a floor of 0.78 and sells 25 shares.

/open <market> <usdc> <max_price>
• /open will-trump-win-2028 yes 20 0.55
  FAK buy for 20 USDC, paying at most 0.55.

/close <market> [shares] [floor]
• /close will-trump-win-2028 yes
  Sells everything at a floor of bid minus {format_price(close_slippage)}.
• /close will-trump-win-2028 yes 10 0.48
  Sells 10 shares at a floor of 0.48.

/price <market>
• /price will-trump-win-2028 yes

/edit <id> <trigger> [floor]
• /edit 3 0.42 0.38
  Only works while the trigger is armed. Ids can be written 3 or #3.

/cancel <id>
• /cancel 3

/positions · /orders · /health · /exit · /help
  No arguments. /exit stops the bot and lists the trigger orders it drops."""


def render_health(state: State, now_ms: int) -> str:
    active = _active_triggers(state)
    live_assets = {trigger.asset_id for trigger in active}
    ages = [now_ms - state.books[asset].received_at_ms if asset in state.books else None for asset in live_assets]
    if not ages:
        oldest_age = "n/a"
    elif None in ages:
        oldest_age = "no data yet"
    else:
        oldest_age = format_duration(max(ages))
    feed = f"{EMOJI_FRESH} fresh" if not state.stale_assets else f"{EMOJI_STALE} stale: {len(state.stale_assets)} assets"
    last_data_ms = max((book.received_at_ms for book in state.books.values()), default=None)
    armed_count = sum(trigger.status == TriggerStatus.ARMED for trigger in active)
    return "\n".join(
        [
            f"{EMOJI_HEALTH} Health",
            f"Status: {state.status}",
            f"Feed: {feed}",
            f"Oldest data (live triggers): {oldest_age}",
            f"Last data: {'none' if last_data_ms is None else format_timestamp(last_data_ms)}",
            f"Uptime: {format_duration(now_ms - state.started_at_ms)}",
            f"Triggers: {armed_count} armed, {len(active)} active, {len(state.triggers)} total",
        ]
    )


def render_positions(state: State, now_ms: int) -> str:
    active = _active_triggers(state)
    lines = [f"{EMOJI_POSITIONS} Positions"]
    total_value = total_cost = Decimal(0)
    for asset_id, position in state.positions.items():
        book = state.books.get(asset_id)
        valuation = value_position(position, book)
        total_value += valuation.value
        total_cost += valuation.cost
        if book is None or book.best_bid is None:
            mark = "no bid"
        else:
            mark = format_price(book.best_bid)
            age_ms = now_ms - book.received_at_ms
            if age_ms > state.config.stale_ms:
                mark += f" ({format_duration(age_ms)} old)"
        triggers = [format_trigger(trigger) for trigger in active if trigger.asset_id == asset_id]
        lines += [
            market_label(state.markets.get(asset_id), asset_id),
            f"  {format_shares(position.size)} shares · avg {format_price(position.avg_price)} · mark {mark}",
            f"  value {format_usdc(valuation.value)} · PnL {valuation.pnl:+.2f} ({valuation.pnl_pct:+.2f}%)",
            f"  triggers: {', '.join(triggers) if triggers else 'none (unprotected)'}",
        ]
    if not state.positions:
        lines.append("No open positions")
    total_pnl = total_value - total_cost
    total_pnl_pct = total_pnl / total_cost * 100 if total_cost else Decimal(0)
    collateral = "unknown" if state.collateral_usdc is None else format_usdc(state.collateral_usdc)
    lines += [
        f"Total value {format_usdc(total_value)} · cost {format_usdc(total_cost)} · "
        f"PnL {total_pnl:+.2f} ({total_pnl_pct:+.2f}%)",
        f"USDC: {collateral}",
    ]
    return "\n".join(lines)


def render_orders(state: State) -> str:
    active = _active_triggers(state)
    if not active:
        return f"{EMOJI_ORDERS} No active trigger orders"
    lines = [f"{EMOJI_ORDERS} Orders"]
    for trigger in active:
        position = state.positions.get(trigger.asset_id)
        remaining = remaining_shares(trigger, position.size if position else Decimal(0))
        book = state.books.get(trigger.asset_id)
        best_bid = book.best_bid if book else None
        bid = "no bid" if best_bid is None else format_price(best_bid)
        lines += [
            f"#{trigger.trigger_id} {trigger.kind} {market_label(state.markets.get(trigger.asset_id), trigger.asset_id)}"
            f" · {trigger.status} {trigger.policy}",
            f"  trigger {format_price(trigger.trigger_price)} · floor {format_price(trigger.floor_price)}"
            f" · size {format_trigger_size(trigger)} · remaining {format_shares(remaining)}",
            f"  bid {bid} · distance {format_distance(best_bid, trigger.trigger_price)}",
        ]
    return "\n".join(lines)


def _book_level(price: Decimal | None, size: Decimal | None) -> str:
    if price is None:
        return "none"
    return format_price(price) if size is None else f"{format_price(price)} × {format_shares(size)}"


def render_price(market: MarketInfo, book: Book) -> str:
    bid, ask = book.best_bid, book.best_ask
    if bid is not None and ask is not None:
        mid_spread = f"Mid {format_price((bid + ask) / 2)} · spread {format_price(ask - bid)}"
    else:
        mid_spread = "Mid n/a · spread n/a"
    last_trade = "n/a" if book.last_trade_price is None else format_price(book.last_trade_price)
    return "\n".join(
        [
            f"{EMOJI_PRICE} {market_label(market, market.asset_id)}",
            f"Bid {_book_level(bid, book.best_bid_size)}",
            f"Ask {_book_level(ask, book.best_ask_size)}",
            mid_spread,
            f"Last trade {last_trade}",
        ]
    )
