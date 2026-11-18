from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from itertools import takewhile
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
class PlanTrade:
    market: MarketRef
    entry_price: Decimal
    stop_loss_price: Decimal  # 0 = no stop, held to resolution
    take_profit_price: Decimal  # 1 = held to resolution
    amount_usdc: Decimal


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
class StopBot:
    pass


@dataclass(frozen=True)
class ShowHelp:
    pass


@dataclass(frozen=True)
class ParseError:
    message: str


UserRequest = (
    ShowHealth
    | ShowPrice
    | ShowPositions
    | OpenPosition
    | PlanTrade
    | ClosePosition
    | SetStopLoss
    | SetTakeProfit
    | ShowOrders
    | EditTrigger
    | CancelTrigger
    | StopBot
    | ShowHelp
)


class _InvalidArguments(Exception):
    pass


def parse_request(text: str) -> UserRequest | ParseError:
    tokens = text.split()
    if not tokens:
        return ParseError(HELP_TEXT)
    # Telegram appends @botname to commands sent in groups.
    command = tokens[0].split("@", 1)[0].lower()
    if command not in COMMANDS:
        return ParseError(HELP_TEXT)
    usage, parse = COMMANDS[command]
    try:
        return parse(tokens[1:])
    except _InvalidArguments as error:
        return ParseError(f"{error}\nUsage: {usage}")


def _take_market(args: list[str]) -> tuple[MarketRef, list[str]]:
    if not args:
        raise _InvalidArguments("missing market")
    first = args[0]
    if first.isdecimal():
        return MarketRef(asset_id=first), args[1:]
    if first.lower().startswith("http"):
        return MarketRef(url=first), args[1:]
    # ponytail: outcome runs until the first number, so labels like "Over 2.5" need the URL or asset id
    words = list(takewhile(lambda arg: not _is_number(arg), args[1:]))
    if not words:
        raise _InvalidArguments("missing outcome after slug")
    return MarketRef(slug=first, outcome=" ".join(words)), args[1 + len(words) :]


def _is_number(token: str) -> bool:
    try:
        Decimal(token)
    except InvalidOperation:
        return False
    return True


def _positional(args: list[str], required: tuple[str, ...], optional: tuple[str, ...] = ()) -> list[str | None]:
    if len(args) < len(required):
        raise _InvalidArguments(f"missing {required[len(args)]}")
    allowed = len(required) + len(optional)
    if len(args) > allowed:
        raise _InvalidArguments(f"unexpected argument {args[allowed]}")
    return [*args, *[None] * (allowed - len(args))]


def _decimal(token: str, name: str) -> Decimal:
    try:
        return Decimal(token)
    except InvalidOperation:
        raise _InvalidArguments(f"{name} must be a number, got {token}") from None


def _number(token: str, name: str) -> Decimal:
    value = _decimal(token, name)
    if not value.is_finite() or value <= 0:
        raise _InvalidArguments(f"{name} must be a positive number, got {token}")
    return value


def _probability(token: str, name: str) -> Decimal:
    value = _decimal(token, name)
    if not value.is_finite() or not 0 <= value <= 1:
        raise _InvalidArguments(f"{name} must be between 0 and 1, got {token}")
    return value


def _price(token: str, name: str) -> Decimal:
    value = _number(token, name)
    if value >= 1:
        raise _InvalidArguments(f"{name} must be < 1, got {token}")
    return value


def _optional(convert: Callable[[str, str], Decimal], token: str | None, name: str) -> Decimal | None:
    return None if token is None else convert(token, name)


def _trigger_id(token: str) -> int:
    digits = token.removeprefix("#")
    if not digits.isdecimal() or int(digits) == 0:
        raise _InvalidArguments(f"id must be a positive integer like 3 or #3, got {token}")
    return int(digits)


def _without_arguments(request: UserRequest) -> Callable[[list[str]], UserRequest]:
    def parse(args: list[str]) -> UserRequest:
        _positional(args, ())
        return request

    return parse


def _parse_price(args: list[str]) -> ShowPrice:
    market, rest = _take_market(args)
    _positional(rest, ())
    return ShowPrice(market)


def _parse_open(args: list[str]) -> OpenPosition:
    market, rest = _take_market(args)
    usdc, max_price = _positional(rest, ("usdc", "max_price"))
    return OpenPosition(market, _number(usdc, "usdc"), _price(max_price, "max_price"))


def _parse_close(args: list[str]) -> ClosePosition:
    market, rest = _take_market(args)
    shares, floor = _positional(rest, (), ("shares", "floor"))
    return ClosePosition(market, _optional(_number, shares, "shares"), _optional(_price, floor, "floor"))


def _parse_stop_loss(args: list[str]) -> SetStopLoss:
    market, rest = _take_market(args)
    policy = rest.pop().lower() if rest and rest[-1].lower() in ("sell", "alert") else None
    trigger, floor, shares = _positional(rest, ("trigger", "floor"), ("shares",))
    return SetStopLoss(
        market, _price(trigger, "trigger"), _price(floor, "floor"), _optional(_number, shares, "shares"), policy
    )


def _parse_take_profit(args: list[str]) -> SetTakeProfit:
    market, rest = _take_market(args)
    trigger, floor, shares = _positional(rest, ("trigger",), ("floor", "shares"))
    trigger_price = _price(trigger, "trigger")
    if floor is not None and shares is None and _number(floor, "floor") >= 1:
        market_text = " ".join(args[: len(args) - len(rest)])
        raise _InvalidArguments(
            f"floor must be < 1 — to set size, pass the floor: /tp {market_text} {trigger} {trigger} {floor}"
        )
    return SetTakeProfit(market, trigger_price, _optional(_price, floor, "floor"), _optional(_number, shares, "shares"))


def _parse_plan(args: list[str]) -> PlanTrade:
    market, rest = _take_market(args)
    entry, stop_loss, take_profit, usdc = _positional(rest, ("entry", "sl", "tp", "usdc"))
    entry_price = _probability(entry, "entry")
    stop_loss_price = _probability(stop_loss, "sl")
    take_profit_price = _probability(take_profit, "tp")
    if not stop_loss_price < entry_price < take_profit_price:
        raise _InvalidArguments(
            f"prices must satisfy 0 <= sl < entry < tp <= 1 (entry {entry}, sl {stop_loss}, tp {take_profit})"
        )
    return PlanTrade(market, entry_price, stop_loss_price, take_profit_price, _number(usdc, "usdc"))


def _parse_edit(args: list[str]) -> EditTrigger:
    trigger_id, trigger, floor = _positional(args, ("id", "trigger"), ("floor",))
    return EditTrigger(_trigger_id(trigger_id), _price(trigger, "trigger"), _optional(_price, floor, "floor"))


def _parse_cancel(args: list[str]) -> CancelTrigger:
    (trigger_id,) = _positional(args, ("id",))
    return CancelTrigger(_trigger_id(trigger_id))


COMMANDS: dict[str, tuple[str, Callable[[list[str]], UserRequest]]] = {
    "/health": ("/health", _without_arguments(ShowHealth())),
    "/price": ("/price <market>", _parse_price),
    "/positions": ("/positions", _without_arguments(ShowPositions())),
    "/open": ("/open <market> <usdc> <max_price>", _parse_open),
    "/close": ("/close <market> [shares] [floor]", _parse_close),
    "/sl": ("/sl <market> <trigger> <floor> [shares] [sell|alert]", _parse_stop_loss),
    "/tp": ("/tp <market> <trigger> [floor] [shares]", _parse_take_profit),
    "/plan": ("/plan <market> <entry> <sl> <tp> <usdc>", _parse_plan),
    "/orders": ("/orders", _without_arguments(ShowOrders())),
    "/edit": ("/edit <id> <trigger> [floor]", _parse_edit),
    "/cancel": ("/cancel <id>", _parse_cancel),
    "/exit": ("/exit", _without_arguments(StopBot())),
    "/help": ("/help", _without_arguments(ShowHelp())),
}

HELP_TEXT = "\n".join(
    [
        "Commands:",
        *(usage for usage, _ in COMMANDS.values()),
        "<market> = asset id, Polymarket URL, or <slug> <outcome>",
    ]
)
