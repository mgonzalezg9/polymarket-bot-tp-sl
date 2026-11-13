from src.core import Book, MarketInfo, State

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


def market_label(market: MarketInfo | None, asset_id: str) -> str:
    if market is None or not market.slug:
        return f"…{asset_id[-6:]}"
    return f"{market.slug} {market.outcome}"


def render_health(state: State, now_ms: int) -> str:
    return f"{EMOJI_HEALTH} Health"


def render_positions(state: State) -> str:
    return f"{EMOJI_POSITIONS} Positions"


def render_orders(state: State) -> str:
    return f"{EMOJI_ORDERS} Orders"


def render_price(market: MarketInfo, book: Book) -> str:
    return f"{EMOJI_PRICE} {market_label(market, market.asset_id)}"
