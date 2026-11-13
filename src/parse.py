from dataclasses import dataclass
from decimal import Decimal
from typing import Literal


@dataclass(frozen=True)
class MarketRef:
    asset_id: str | None = None
    url: str | None = None
    slug: str | None = None
    outcome: str | None = None


@dataclass(frozen=True)
class ShowHealth:
    pass


@dataclass(frozen=True)
class ShowPrice:
    market: MarketRef


@dataclass(frozen=True)
class ShowPositions:
    pass


@dataclass(frozen=True)
class OpenPosition:
    market: MarketRef
    amount_usdc: Decimal
    max_price: Decimal


@dataclass(frozen=True)
class ClosePosition:
    market: MarketRef
    shares: Decimal | None = None  # None = whole position
    floor_price: Decimal | None = None  # None = best_bid - CLOSE_SLIPPAGE


@dataclass(frozen=True)
class SetStopLoss:
    market: MarketRef
    trigger_price: Decimal
    floor_price: Decimal
    size: Decimal | None = None  # None = whole position at fire time
    policy: Literal["sell", "alert"] | None = None  # None = sell


@dataclass(frozen=True)
class SetTakeProfit:
    market: MarketRef
    trigger_price: Decimal
    floor_price: Decimal | None = None  # None = trigger_price
    size: Decimal | None = None


@dataclass(frozen=True)
class ShowOrders:
    pass


@dataclass(frozen=True)
class EditTrigger:
    trigger_id: int
    trigger_price: Decimal
    floor_price: Decimal | None = None


@dataclass(frozen=True)
class CancelTrigger:
    trigger_id: int


@dataclass(frozen=True)
class ParseError:
    message: str


UserRequest = (
    ShowHealth
    | ShowPrice
    | ShowPositions
    | OpenPosition
    | ClosePosition
    | SetStopLoss
    | SetTakeProfit
    | ShowOrders
    | EditTrigger
    | CancelTrigger
)


def parse_request(text: str) -> UserRequest | ParseError:
    raise NotImplementedError
