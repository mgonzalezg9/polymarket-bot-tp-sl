import json
from collections.abc import Callable, Sequence
from dataclasses import replace
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path

from src.core import (
    BalanceLoaded,
    Book,
    Event,
    FillsReconciled,
    MarketInfo,
    OrderFilled,
    OrderPolled,
    OrderRejected,
    Position,
    PositionsLoaded,
)
from src.parse import MarketRef

DEFAULT_COLLATERAL_USDC = Decimal("1000")
SHARE_STEP = Decimal("0.01")

Level = tuple[Decimal, Decimal]


def read_replay(path: str | Path) -> list[dict]:
    with open(path) as replay_file:
        return [json.loads(line, parse_float=Decimal) for line in replay_file if line.strip()]


def fill_against_levels(
    levels: list[Level], quantity: Decimal, is_acceptable: Callable[[Decimal], bool]
) -> tuple[Decimal, Decimal, list[Level]]:
    """Returns (filled_shares, notional_usdc, remaining_levels)."""
    filled_shares = notional_usdc = Decimal(0)
    remaining_levels = list(levels)
    while remaining_levels and filled_shares < quantity and is_acceptable(remaining_levels[0][0]):
        price, size = remaining_levels.pop(0)
        taken = min(size, quantity - filled_shares)
        filled_shares += taken
        notional_usdc += taken * price
        if taken < size:
            remaining_levels.insert(0, (price, size - taken))
    return filled_shares, notional_usdc, remaining_levels


def shares_for_budget(levels: list[Level], budget_usdc: Decimal, is_acceptable: Callable[[Decimal], bool]) -> Decimal:
    shares = Decimal(0)
    for price, size in levels:
        if not is_acceptable(price):
            break
        taken = min(size, (budget_usdc / price).quantize(SHARE_STEP, rounding=ROUND_FLOOR))
        shares += taken
        budget_usdc -= taken * price
        if taken < size:
            break
    return shares


def _parse_levels(raw_levels: list) -> list[Level]:
    return [(Decimal(price), Decimal(size)) for price, size in raw_levels]


class PaperExchange:
    def __init__(self) -> None:
        self.markets: dict[str, MarketInfo] = {}
        self.positions: dict[str, Position] = {}
        self.collateral_usdc = DEFAULT_COLLATERAL_USDC
        self.bids: dict[str, list[Level]] = {}
        self.asks: dict[str, list[Level]] = {}
        self.last_trade_prices: dict[str, Decimal | None] = {}
        self.clock_ms = 0

    def load_line(self, line: dict) -> Book | None:
        if "ts" in line:
            self.clock_ms = max(self.clock_ms, line["ts"])
        match line["type"]:
            case "market":
                self.markets[line["asset_id"]] = MarketInfo(
                    asset_id=line["asset_id"],
                    slug=line["slug"],
                    outcome=line["outcome"],
                    tick_size=Decimal(line["tick_size"]),
                    min_order_size=Decimal(line["min_order_size"]),
                    is_v2=line.get("is_v2", False),
                )
            case "position":
                self.positions[line["asset_id"]] = Position(
                    asset_id=line["asset_id"], size=Decimal(line["size"]), avg_price=Decimal(line["avg_price"])
                )
            case "collateral":
                self.collateral_usdc = Decimal(line["usdc"])
            case "book":
                asset_id = line["asset_id"]
                self.bids[asset_id] = sorted(_parse_levels(line["bids"]), reverse=True)
                self.asks[asset_id] = sorted(_parse_levels(line["asks"]))
                last_trade_price = line.get("last_trade_price")
                self.last_trade_prices[asset_id] = None if last_trade_price is None else Decimal(last_trade_price)
                return self._book(asset_id, line["ts"])
        return None

    def _book(self, asset_id: str, received_at_ms: int) -> Book:
        bids = self.bids.get(asset_id, [])
        asks = self.asks.get(asset_id, [])
        return Book(
            best_bid=bids[0][0] if bids else None,
            best_bid_size=bids[0][1] if bids else None,
            best_ask=asks[0][0] if asks else None,
            best_ask_size=asks[0][1] if asks else None,
            last_trade_price=self.last_trade_prices.get(asset_id),
            received_at_ms=received_at_ms,
        )

    def _held(self, asset_id: str) -> Position:
        return self.positions.get(asset_id, Position(asset_id=asset_id, size=Decimal(0), avg_price=Decimal(0)))

    # MarketData

    async def subscribe(self, asset_ids: Sequence[str], publish: Callable[[Event], None]) -> None:
        pass  # ponytail: replay pushes books itself

    async def get_book(self, asset_id: str) -> Book:
        return self._book(asset_id, self.clock_ms)

    async def resolve(self, ref: MarketRef) -> tuple[MarketInfo, Book]:
        if ref.url is not None:
            raise ValueError("URLs need the network; in dry-run use <slug> <outcome> or an asset id")
        if ref.asset_id is not None:
            market = self.markets.get(ref.asset_id)
        else:
            market = next(
                (
                    market
                    for market in self.markets.values()
                    if market.slug == ref.slug and market.outcome.lower() == (ref.outcome or "").lower()
                ),
                None,
            )
        if market is None:
            raise ValueError(f"Market not found in replay: {ref.asset_id or f'{ref.slug} {ref.outcome}'}")
        return market, self._book(market.asset_id, self.clock_ms)

    # Execution

    async def sell_fak(self, request_id: str, asset_id: str, shares: Decimal, floor_price: Decimal, at_ms: int) -> Event:
        position = self._held(asset_id)
        if shares > position.size:
            return OrderRejected(at_ms=at_ms, request_id=request_id, code="not_enough_balance")
        filled_shares, filled_usdc, remaining_bids = fill_against_levels(
            self.bids.get(asset_id, []), shares, lambda price: price >= floor_price
        )
        if filled_shares == 0:
            return OrderRejected(at_ms=at_ms, request_id=request_id, code="fak_not_filled")
        self.bids[asset_id] = remaining_bids
        self.positions[asset_id] = replace(position, size=position.size - filled_shares)
        self.collateral_usdc += filled_usdc
        return self._filled(request_id, filled_shares, filled_usdc, at_ms)

    async def buy_fak(self, request_id: str, asset_id: str, amount_usdc: Decimal, max_price: Decimal, at_ms: int) -> Event:
        if amount_usdc > self.collateral_usdc:
            return OrderRejected(at_ms=at_ms, request_id=request_id, code="not_enough_balance")
        asks = self.asks.get(asset_id, [])
        is_acceptable = lambda price: price <= max_price
        filled_shares, filled_usdc, remaining_asks = fill_against_levels(
            asks, shares_for_budget(asks, amount_usdc, is_acceptable), is_acceptable
        )
        if filled_shares == 0:
            return OrderRejected(at_ms=at_ms, request_id=request_id, code="fak_not_filled")
        self.asks[asset_id] = remaining_asks
        position = self._held(asset_id)
        new_size = position.size + filled_shares
        self.positions[asset_id] = replace(
            position, size=new_size, avg_price=(position.size * position.avg_price + filled_usdc) / new_size
        )
        self.collateral_usdc -= filled_usdc
        return self._filled(request_id, filled_shares, filled_usdc, at_ms)

    @staticmethod
    def _filled(request_id: str, filled_shares: Decimal, filled_usdc: Decimal, at_ms: int) -> OrderFilled:
        return OrderFilled(
            at_ms=at_ms,
            request_id=request_id,
            filled_shares=filled_shares,
            filled_usdc=filled_usdc,
            order_id=f"paper-{request_id}",
            trade_ids=(f"paper-{request_id}",),
        )

    # ponytail: paper orders are never delayed or unknown, so polls and reconciles find nothing new
    async def get_order_fill(self, request_id: str, order_id: str, at_ms: int) -> Event:
        return OrderPolled(
            at_ms=at_ms, request_id=request_id, filled_shares=Decimal(0), filled_usdc=Decimal(0), is_final=True
        )

    async def sum_recent_fills(
        self,
        request_id: str,
        asset_id: str,
        side: str,
        since_ms: int,
        exclude_trade_ids: frozenset[str],
        at_ms: int,
    ) -> Event:
        return FillsReconciled(
            at_ms=at_ms, request_id=request_id, filled_shares=Decimal(0), filled_usdc=Decimal(0), trade_ids=()
        )

    # Account

    async def load_positions(self, reply_chat_id: int | None, at_ms: int) -> Event:
        held = tuple(position for position in self.positions.values() if position.size > 0)
        return PositionsLoaded(
            at_ms=at_ms,
            positions=held,
            markets=tuple(self.markets[position.asset_id] for position in held if position.asset_id in self.markets),
            books=tuple((position.asset_id, self._book(position.asset_id, self.clock_ms)) for position in held),
            collateral_usdc=self.collateral_usdc,
            reply_chat_id=reply_chat_id,
        )

    async def get_balance(self, asset_id: str, at_ms: int) -> Event:
        return BalanceLoaded(at_ms=at_ms, asset_id=asset_id, size=self._held(asset_id).size)
