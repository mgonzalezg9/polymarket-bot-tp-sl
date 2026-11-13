import asyncio
import json
import logging
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, TextIO

from polymarket import (
    AsyncSecureClient,
    InsufficientAllowanceError,
    InsufficientLiquidityError,
    Market,
    OrderBookLevel,
    PolymarketError,
    ProtocolVersion,
    RequestRejectedError,
    SigningError,
    UserInputError,
)
from polymarket.models.gamma.market import MarketOutcome
from polymarket.streams import (
    MarketBookEvent,
    MarketLastTradePriceEvent,
    MarketPriceChangeEvent,
    MarketResolvedEvent,
    MarketSpec,
    MarketTickSizeChangeEvent,
)

from constants import STREAM_RETRY_SECONDS
from src.core import (
    BalanceFetchFailed,
    BalanceLoaded,
    Book,
    BookUpdated,
    CheckFailed,
    Event,
    FatalErrorOccurred,
    FillsReconciled,
    MarketInfo,
    MarketResolved,
    OrderDelayed,
    OrderFilled,
    OrderOutcomeUnknown,
    OrderPolled,
    OrderRejected,
    Position,
    PositionsFetchFailed,
    PositionsLoaded,
    TickSizeChanged,
)
from src.parse import MarketRef

logger = logging.getLogger(__name__)

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def base_units_to_decimal(amount: int) -> Decimal:
    return Decimal(amount).scaleb(-6)


def find_outcome(market: Market, label: str | None = None, asset_id: str | None = None) -> MarketOutcome | None:
    for outcome in (market.outcomes.yes, market.outcomes.no):
        if label is not None and outcome.label.lower() == label.lower():
            return outcome
        if asset_id is not None and asset_id in (outcome.token_id, outcome.position_id):
            return outcome
    return None


def trading_asset_id(market: Market, outcome: MarketOutcome) -> str:
    asset_id = outcome.position_id if market.version == ProtocolVersion.V2 else outcome.token_id
    if asset_id is None:
        raise ValueError(f"{market.slug} {outcome.label} has no tradable asset id")
    return asset_id


def order_rejection_code(error: RequestRejectedError) -> str:
    # The CLOB reports most order failures as HTTP 400 with only a message.
    message = str(error).lower()
    if "not enough balance" in message:
        return "not_enough_balance"
    if "no orders found to match with fak order" in message:
        return "fak_not_filled"
    return error.code or "unknown"


def not_posted(at_ms: int, request_id: str, code: str, error: Exception) -> OrderRejected:
    logger.warning("Order %s not posted (%s): %s", request_id, code, error)
    return OrderRejected(at_ms, request_id, code)


class PolymarketAdapter:
    """Live MarketData, Execution and Account over one AsyncSecureClient."""

    def __init__(self, client: AsyncSecureClient, now_ms: Callable[[], int], record_file: TextIO | None = None) -> None:
        self._client = client
        self._now_ms = now_ms
        self._record_file = record_file
        self._positions_recorded = False
        self._markets: dict[str, MarketInfo] = {}
        self._bids: dict[str, dict[Decimal, Decimal]] = {}
        self._asks: dict[str, dict[Decimal, Decimal]] = {}
        self._last_trade_prices: dict[str, Decimal] = {}

    # MARKET DATA

    async def subscribe(self, asset_ids: Sequence[str], publish: Callable[[Event], None]) -> None:
        spec = MarketSpec(asset_ids=list(asset_ids), custom_feature_enabled=True)
        while True:
            try:
                async with await self._client.subscribe(spec) as handle:
                    async for event in handle:
                        self._apply_market_event(event, publish)
            except Exception:
                logger.exception("Market stream for %s failed", list(asset_ids))
            logger.warning("Resubscribing %s in %ss", list(asset_ids), STREAM_RETRY_SECONDS)
            await asyncio.sleep(STREAM_RETRY_SECONDS)

    async def get_book(self, asset_id: str) -> Book:
        order_book = await self._client.get_order_book(asset_id=asset_id)
        return self._replace_book(asset_id, order_book.bids, order_book.asks, order_book.last_trade_price)

    async def resolve(self, ref: MarketRef) -> tuple[MarketInfo, Book]:
        if ref.asset_id is not None:
            if ref.asset_id in self._markets:
                return self._markets[ref.asset_id], await self.get_book(ref.asset_id)
            order_book = await self._client.get_order_book(asset_id=ref.asset_id)
            return await self._load_by_condition(order_book.condition_id, ref.asset_id)
        if ref.url is not None:
            market = await self._client.get_market(url=ref.url)
        else:
            market = await self._market_by_slug(ref.slug or "")
        if ref.outcome is not None:
            outcome = find_outcome(market, label=ref.outcome)
        else:
            known = [outcome for outcome in (market.outcomes.yes, market.outcomes.no) if trading_asset_id(market, outcome) in self._markets]
            outcome = known[0] if len(known) == 1 else None
        if outcome is None:
            labels = f"{market.outcomes.yes.label}|{market.outcomes.no.label}"
            raise ValueError(f"Pick an outcome: {market.slug} {labels}")
        return await self._load_market(market, outcome)

    async def _market_by_slug(self, slug: str) -> Market:
        try:
            return await self._client.get_market(slug=slug)
        except RequestRejectedError as error:
            if error.status != 404:
                raise
        event = await self._client.get_event(slug=slug)
        if len(event.markets) == 1:
            return event.markets[0]
        choices = "\n".join(f"{market.group_item_title} → {market.slug}" for market in event.markets)
        raise ValueError(f"{slug} is an event with {len(event.markets)} markets, use one of:\n{choices}")

    async def _load_by_condition(self, condition_id: str, asset_id: str) -> tuple[MarketInfo, Book]:
        page = await self._client.list_markets(condition_ids=condition_id).first_page()
        for market in page.items:
            if (outcome := find_outcome(market, asset_id=asset_id)) is not None:
                return await self._load_market(market, outcome)
        raise ValueError(f"Unknown asset id {asset_id}, use <slug> <outcome>")

    async def _load_market(self, market: Market, outcome: MarketOutcome) -> tuple[MarketInfo, Book]:
        asset_id = trading_asset_id(market, outcome)
        order_book = await self._client.get_order_book(asset_id=asset_id)
        info = MarketInfo(
            asset_id=asset_id,
            slug=market.slug or "",
            outcome=outcome.label,
            tick_size=order_book.tick_size,
            min_order_size=order_book.min_order_size,
            is_v2=market.version == ProtocolVersion.V2,
        )
        if asset_id not in self._markets:
            self._record(
                {
                    "type": "market",
                    "asset_id": asset_id,
                    "slug": info.slug,
                    "outcome": info.outcome,
                    "tick_size": str(info.tick_size),
                    "min_order_size": str(info.min_order_size),
                }
            )
        self._markets[asset_id] = info
        return info, self._replace_book(asset_id, order_book.bids, order_book.asks, order_book.last_trade_price)

    def _apply_market_event(self, event: Any, publish: Callable[[Event], None]) -> None:
        payload = event.payload
        match event:
            case MarketBookEvent():
                book = self._replace_book(payload.asset_id, payload.bids, payload.asks, payload.last_trade_price)
                publish(BookUpdated(book.received_at_ms, payload.asset_id, book))
            case MarketPriceChangeEvent():
                changed: dict[str, None] = {}
                for change in payload.price_changes:
                    levels = (self._bids if change.side == "BUY" else self._asks).get(change.asset_id)
                    if levels is None:
                        continue  # no snapshot yet for this asset: the next book event carries it
                    if change.size == 0:
                        levels.pop(change.price, None)
                    else:
                        levels[change.price] = change.size
                    changed[change.asset_id] = None
                for asset_id in changed:
                    book = self._book(asset_id)
                    publish(BookUpdated(book.received_at_ms, asset_id, book))
            case MarketLastTradePriceEvent():
                if payload.asset_id in self._bids:
                    self._last_trade_prices[payload.asset_id] = payload.price
                    book = self._book(payload.asset_id)
                    publish(BookUpdated(book.received_at_ms, payload.asset_id, book))
            case MarketTickSizeChangeEvent():
                publish(TickSizeChanged(self._now_ms(), payload.asset_id, payload.new_tick_size))
            case MarketResolvedEvent():
                publish(MarketResolved(self._now_ms(), tuple(payload.asset_ids or ()), payload.winning_asset_id))

    def _replace_book(
        self,
        asset_id: str,
        bids: Iterable[OrderBookLevel],
        asks: Iterable[OrderBookLevel],
        last_trade_price: Decimal | None,
    ) -> Book:
        self._bids[asset_id] = {level.price: level.size for level in bids if level.size > 0}
        self._asks[asset_id] = {level.price: level.size for level in asks if level.size > 0}
        if last_trade_price is not None:
            self._last_trade_prices[asset_id] = last_trade_price
        return self._book(asset_id)

    def _book(self, asset_id: str) -> Book:
        bids, asks = self._bids[asset_id], self._asks[asset_id]
        best_bid, best_ask = max(bids, default=None), min(asks, default=None)
        last_trade_price = self._last_trade_prices.get(asset_id)
        book = Book(
            best_bid=best_bid,
            best_bid_size=bids.get(best_bid),
            best_ask=best_ask,
            best_ask_size=asks.get(best_ask),
            last_trade_price=last_trade_price,
            received_at_ms=self._now_ms(),
        )
        self._record(
            {
                "type": "book",
                "ts": book.received_at_ms,
                "asset_id": asset_id,
                "bids": [[str(price), str(bids[price])] for price in sorted(bids, reverse=True)],
                "asks": [[str(price), str(asks[price])] for price in sorted(asks)],
                "last_trade_price": None if last_trade_price is None else str(last_trade_price),
            }
        )
        return book

    def _record(self, line: dict) -> None:
        if self._record_file is not None:
            self._record_file.write(json.dumps(line) + "\n")
            self._record_file.flush()

    # EXECUTION

    async def sell_fak(self, request_id: str, asset_id: str, shares: Decimal, floor_price: Decimal, at_ms: int) -> Event:
        return await self._place_order(
            request_id, at_ms, asset_id=asset_id, side="SELL", shares=shares, min_price=floor_price, order_type="FAK"
        )

    async def buy_fak(self, request_id: str, asset_id: str, amount_usdc: Decimal, max_price: Decimal, at_ms: int) -> Event:
        return await self._place_order(
            request_id,
            at_ms,
            asset_id=asset_id,
            side="BUY",
            amount=amount_usdc,
            max_spend=amount_usdc,
            max_price=max_price,
            order_type="FAK",
        )

    async def _place_order(self, request_id: str, at_ms: int, **order: Any) -> Event:
        try:
            response = await self._client.place_market_order(**order)
        except InsufficientLiquidityError:
            return OrderRejected(at_ms, request_id, "fak_not_filled")
        except UserInputError as error:
            return not_posted(at_ms, request_id, "invalid_input", error)
        except SigningError as error:
            return not_posted(at_ms, request_id, "signing_error", error)
        except InsufficientAllowanceError as error:
            return not_posted(at_ms, request_id, "insufficient_allowance", error)
        except RequestRejectedError as error:
            logger.warning("Order %s rejected: status=%s code=%s %s", request_id, error.status, error.code, error)
            if error.status in (401, 403) or error.restriction is not None:
                return FatalErrorOccurred(at_ms, f"order rejected ({error.status}): {error}")
            if error.status >= 500:
                return OrderOutcomeUnknown(at_ms, request_id, str(error))
            return OrderRejected(at_ms, request_id, order_rejection_code(error))
        # Transport, timeout, rate limit, malformed response: the order may have been posted.
        except PolymarketError as error:
            logger.warning("Order %s outcome unknown: %r", request_id, error)
            return OrderOutcomeUnknown(at_ms, request_id, str(error) or type(error).__name__)
        if not response.ok:
            return OrderRejected(at_ms, request_id, response.code)
        if response.status != "matched":
            return OrderDelayed(at_ms, request_id, response.order_id)
        if order["side"] == "SELL":
            shares, usdc = response.making_amount, response.taking_amount
        else:
            shares, usdc = response.taking_amount, response.making_amount
        return OrderFilled(at_ms, request_id, shares, usdc, response.order_id, response.trade_ids)

    async def get_order_fill(self, request_id: str, order_id: str, at_ms: int) -> Event:
        try:
            order = await self._client.get_order(order_id=order_id)
        except RequestRejectedError as error:
            if error.status == 404:
                return OrderOutcomeUnknown(at_ms, request_id, f"order {order_id} not found")
            return CheckFailed(at_ms, request_id, str(error))
        except PolymarketError as error:
            return CheckFailed(at_ms, request_id, str(error) or type(error).__name__)
        return OrderPolled(
            at_ms,
            request_id,
            filled_shares=order.size_matched,
            filled_usdc=order.size_matched * order.price,
            is_final=order.status.upper() not in {"LIVE", "DELAYED"},
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
        since = EPOCH + timedelta(milliseconds=since_ms)
        try:
            page = await self._client.list_account_trades(asset_id=asset_id).first_page()
        except PolymarketError as error:
            return CheckFailed(at_ms, request_id, str(error) or type(error).__name__)
        trades = [
            trade
            for trade in page.items
            if trade.trader_side == "TAKER"
            and trade.side == side
            and trade.status != "FAILED"
            and trade.matched_at >= since
            and trade.id not in exclude_trade_ids
        ]
        logger.warning("Reconciled %s from %d trades since %s", request_id, len(trades), since.isoformat())
        return FillsReconciled(
            at_ms,
            request_id,
            filled_shares=sum((trade.size for trade in trades), Decimal(0)),
            filled_usdc=sum((trade.size * trade.price for trade in trades), Decimal(0)),
            trade_ids=tuple(trade.id for trade in trades),
        )

    # ACCOUNT

    async def load_positions(self, reply_chat_id: int | None, at_ms: int) -> Event:
        try:
            held = [
                position
                async for position in self._client.list_positions(status="OPEN").iter_items()
                if position.current_size > 0
            ]
            results = await asyncio.gather(
                *(self._load_by_condition(position.condition_id, position.asset_id) for position in held),
                return_exceptions=True,
            )
            collateral = await self._client.get_balance_allowance(asset_type="COLLATERAL")
            loaded: list[tuple[Position, MarketInfo, Book]] = []
            for position, result in zip(held, results):
                if isinstance(result, ValueError):
                    # ponytail: an unmappable position is skipped, not fatal; it is only visible in the log
                    logger.warning("Skipping position %s (%s): %s", position.title, position.asset_id, result)
                elif isinstance(result, BaseException):
                    raise result
                else:
                    info, book = result
                    loaded.append((Position(info.asset_id, position.current_size, position.avg_price), info, book))
        except PolymarketError as error:
            return PositionsFetchFailed(at_ms, str(error) or type(error).__name__, reply_chat_id)
        collateral_usdc = base_units_to_decimal(collateral.balance)
        if not self._positions_recorded:
            self._positions_recorded = True
            for position, _, _ in loaded:
                self._record(
                    {
                        "type": "position",
                        "asset_id": position.asset_id,
                        "size": str(position.size),
                        "avg_price": str(position.avg_price),
                    }
                )
            self._record({"type": "collateral", "usdc": str(collateral_usdc)})
        return PositionsLoaded(
            at_ms,
            positions=tuple(position for position, _, _ in loaded),
            markets=tuple(info for _, info, _ in loaded),
            books=tuple((info.asset_id, book) for _, info, book in loaded),
            collateral_usdc=collateral_usdc,
            reply_chat_id=reply_chat_id,
        )

    async def get_balance(self, asset_id: str, at_ms: int) -> Event:
        market = self._markets.get(asset_id)
        if market is None:
            return BalanceFetchFailed(at_ms, asset_id, "unknown market, cannot pick CONDITIONAL vs CONDITIONAL-V2")
        asset_type = "CONDITIONAL-V2" if market.is_v2 else "CONDITIONAL"
        try:
            result = await self._client.get_balance_allowance(asset_type=asset_type, asset_id=asset_id)
        except PolymarketError as error:
            return BalanceFetchFailed(at_ms, asset_id, str(error) or type(error).__name__)
        return BalanceLoaded(at_ms, asset_id, base_units_to_decimal(result.balance))
