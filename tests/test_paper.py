import asyncio
from decimal import Decimal
from pathlib import Path

import pytest

from src.core import BalanceLoaded, OrderFilled, OrderRejected, PositionsLoaded
from src.paper import PaperExchange, fill_against_levels, read_replay
from src.parse import MarketRef
from tests.helpers import ASSET_ID, T0_MS, make_market

REPLAYS_DIR = Path(__file__).parent.parent / "replays"
BIDS = [["0.52", "60"], ["0.50", "80"], ["0.45", "500"]]
ASKS = [["0.54", "50"], ["0.56", "100"], ["0.60", "500"]]


def book_line(bids=BIDS, asks=ASKS, ts=T0_MS) -> dict:
    return {"type": "book", "ts": ts, "asset_id": ASSET_ID, "bids": bids, "asks": asks, "last_trade_price": "0.53"}


def make_exchange(position_size: str = "100") -> PaperExchange:
    exchange = PaperExchange()
    exchange.load_line(
        {
            "type": "market",
            "asset_id": ASSET_ID,
            "slug": "will-x-happen",
            "outcome": "Yes",
            "tick_size": "0.01",
            "min_order_size": "5",
        }
    )
    exchange.load_line({"type": "position", "asset_id": ASSET_ID, "size": position_size, "avg_price": "0.55"})
    exchange.load_line(book_line())
    return exchange


def sell(exchange: PaperExchange, shares: str, floor_price: str):
    return asyncio.run(exchange.sell_fak("r1", ASSET_ID, Decimal(shares), Decimal(floor_price), T0_MS))


def buy(exchange: PaperExchange, amount_usdc: str, max_price: str):
    return asyncio.run(exchange.buy_fak("r1", ASSET_ID, Decimal(amount_usdc), Decimal(max_price), T0_MS))


def test_fill_against_levels_walks_best_first_and_stops_when_unacceptable():
    levels = [(Decimal("0.52"), Decimal("60")), (Decimal("0.50"), Decimal("80")), (Decimal("0.45"), Decimal("500"))]

    filled_shares, notional_usdc, remaining_levels = fill_against_levels(
        levels, Decimal("1000"), lambda price: price >= Decimal("0.50")
    )

    assert filled_shares == Decimal("140")
    assert notional_usdc == Decimal("71.2")
    assert remaining_levels == [(Decimal("0.45"), Decimal("500"))]


def test_sell_fak_fills_across_levels_and_credits_usdc():
    exchange = make_exchange()

    result = sell(exchange, "100", "0.50")

    assert result == OrderFilled(
        at_ms=T0_MS,
        request_id="r1",
        filled_shares=Decimal("100"),
        filled_usdc=Decimal("51.20"),
        order_id="paper-r1",
        trade_ids=("paper-r1",),
    )
    assert exchange.collateral_usdc == Decimal("1051.20")
    assert asyncio.run(exchange.get_balance(ASSET_ID, T0_MS)) == BalanceLoaded(
        at_ms=T0_MS, asset_id=ASSET_ID, size=Decimal(0)
    )
    loaded = asyncio.run(exchange.load_positions(7, T0_MS))
    assert isinstance(loaded, PositionsLoaded)
    assert loaded.positions == () and loaded.reply_chat_id == 7


def test_sell_fak_floor_stops_the_walk_with_a_partial_fill():
    result = sell(make_exchange(), "100", "0.51")

    assert isinstance(result, OrderFilled)
    assert (result.filled_shares, result.filled_usdc) == (Decimal("60"), Decimal("31.20"))


def test_sell_depletes_the_book_until_the_next_book_line():
    exchange = make_exchange()
    sell(exchange, "70", "0.50")
    exchange.load_line({"type": "command", "ts": T0_MS + 5_000, "text": "/orders"})

    depleted = asyncio.run(exchange.get_book(ASSET_ID))
    assert (depleted.best_bid, depleted.best_bid_size, depleted.received_at_ms) == (
        Decimal("0.50"),
        Decimal("70"),
        T0_MS + 5_000,
    )
    assert sell(exchange, "30", "0.51") == OrderRejected(at_ms=T0_MS, request_id="r1", code="fak_not_filled")

    refreshed = exchange.load_line(book_line(ts=T0_MS + 6_000))
    assert (refreshed.best_bid, refreshed.best_bid_size, refreshed.received_at_ms) == (
        Decimal("0.52"),
        Decimal("60"),
        T0_MS + 6_000,
    )


def test_sell_fak_with_no_bid_at_or_above_floor_is_rejected_and_changes_nothing():
    exchange = make_exchange()

    result = sell(exchange, "10", "0.53")

    assert result == OrderRejected(at_ms=T0_MS, request_id="r1", code="fak_not_filled")
    assert asyncio.run(exchange.get_book(ASSET_ID)).best_bid_size == Decimal("60")
    assert exchange.positions[ASSET_ID].size == Decimal("100")


def test_sell_fak_above_balance_is_rejected():
    result = sell(make_exchange(), "101", "0.40")

    assert result == OrderRejected(at_ms=T0_MS, request_id="r1", code="not_enough_balance")


def test_buy_fak_spends_usdc_across_levels_and_updates_avg_price():
    exchange = make_exchange()

    result = buy(exchange, "41", "0.56")

    assert isinstance(result, OrderFilled)
    assert (result.filled_shares, result.filled_usdc) == (Decimal("75"), Decimal("41"))
    assert exchange.collateral_usdc == Decimal("959")
    position = exchange.positions[ASSET_ID]
    assert position.size == Decimal("175")
    assert position.avg_price == (Decimal("100") * Decimal("0.55") + Decimal("41")) / Decimal("175")
    assert asyncio.run(exchange.get_book(ASSET_ID)).best_ask == Decimal("0.56")


def test_buy_fak_stops_at_max_price():
    result = buy(make_exchange(), "100", "0.54")

    assert isinstance(result, OrderFilled)
    assert (result.filled_shares, result.filled_usdc) == (Decimal("50"), Decimal("27"))


def test_buy_fak_rejections():
    assert buy(make_exchange(), "10", "0.53").code == "fak_not_filled"
    assert buy(make_exchange(), "1001", "0.99").code == "not_enough_balance"


def test_resolve_by_slug_and_outcome_or_asset_id():
    exchange = make_exchange()

    by_slug, book = asyncio.run(exchange.resolve(MarketRef(slug="will-x-happen", outcome="yes")))
    by_asset_id, _ = asyncio.run(exchange.resolve(MarketRef(asset_id=ASSET_ID)))

    assert by_slug == by_asset_id == make_market()
    assert (book.best_bid, book.best_ask, book.received_at_ms) == (Decimal("0.52"), Decimal("0.54"), T0_MS)


@pytest.mark.parametrize(
    "ref",
    [
        MarketRef(url="https://polymarket.com/event/will-x-happen"),
        MarketRef(slug="will-x-happen", outcome="maybe"),
        MarketRef(asset_id="999"),
    ],
)
def test_resolve_unknown_or_url_raises(ref):
    with pytest.raises(ValueError):
        asyncio.run(make_exchange().resolve(ref))


@pytest.mark.parametrize("replay_name", ["sl_gap.jsonl", "tp_partial.jsonl"])
def test_shipped_replay_loads_and_its_commands_resolve(replay_name):
    exchange = PaperExchange()
    lines = read_replay(REPLAYS_DIR / replay_name)

    books = [book for book in map(exchange.load_line, lines) if book is not None]

    assert books and all(isinstance(book.best_bid, Decimal) for book in books)
    for line in lines:
        tokens = line.get("text", "").split()
        if tokens[:1] in (["/sl"], ["/tp"]):
            asyncio.run(exchange.resolve(MarketRef(slug=tokens[1], outcome=tokens[2])))
