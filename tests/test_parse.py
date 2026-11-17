from decimal import Decimal

import pytest

from src.parse import (
    CancelTrigger,
    ClosePosition,
    EditTrigger,
    MarketRef,
    OpenPosition,
    ParseError,
    PlanTrade,
    SetStopLoss,
    SetTakeProfit,
    ShowHealth,
    ShowHelp,
    ShowOrders,
    ShowPositions,
    ShowPrice,
    StopBot,
    parse_request,
)

SLUG = MarketRef(slug="will-trump-win-2028", outcome="yes")
ASSET = MarketRef(asset_id="7134921")
URL = MarketRef(url="https://polymarket.com/event/will-trump-win-2028")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/health", ShowHealth()),
        ("/positions", ShowPositions()),
        ("/orders", ShowOrders()),
        ("/exit", StopBot()),
        ("/help", ShowHelp()),
        ("/price will-trump-win-2028 yes", ShowPrice(SLUG)),
        ("/price 7134921", ShowPrice(ASSET)),
        ("/price https://polymarket.com/event/will-trump-win-2028", ShowPrice(URL)),
        ("/open will-trump-win-2028 yes 20 0.55", OpenPosition(SLUG, Decimal("20"), Decimal("0.55"))),
        ("/close will-trump-win-2028 yes", ClosePosition(SLUG)),
        ("/close 7134921 10", ClosePosition(ASSET, shares=Decimal("10"))),
        ("/close 7134921 10 0.48", ClosePosition(ASSET, shares=Decimal("10"), floor_price=Decimal("0.48"))),
        ("/sl will-trump-win-2028 yes 0.40 0.35", SetStopLoss(SLUG, Decimal("0.40"), Decimal("0.35"))),
        ("/sl 7134921 0.40 0.35 50", SetStopLoss(ASSET, Decimal("0.40"), Decimal("0.35"), size=Decimal("50"))),
        (
            "/sl 7134921 0.40 0.35 50 alert",
            SetStopLoss(ASSET, Decimal("0.40"), Decimal("0.35"), size=Decimal("50"), policy="alert"),
        ),
        ("/sl 7134921 0.40 0.35 SELL", SetStopLoss(ASSET, Decimal("0.40"), Decimal("0.35"), policy="sell")),
        ("/tp will-trump-win-2028 yes 0.80", SetTakeProfit(SLUG, Decimal("0.80"))),
        ("/tp 7134921 0.80 0.78", SetTakeProfit(ASSET, Decimal("0.80"), floor_price=Decimal("0.78"))),
        (
            "/tp https://polymarket.com/event/will-trump-win-2028 0.80 0.78 25",
            SetTakeProfit(URL, Decimal("0.80"), floor_price=Decimal("0.78"), size=Decimal("25")),
        ),
        (
            "/plan will-trump-win-2028 yes 0.70 0.65 1 100",
            PlanTrade(SLUG, Decimal("0.70"), Decimal("0.65"), Decimal("1"), Decimal("100")),
        ),
        ("/plan 7134921 0.70 0 1 100", PlanTrade(ASSET, Decimal("0.70"), Decimal("0"), Decimal("1"), Decimal("100"))),
        ("/edit 3 0.42", EditTrigger(3, Decimal("0.42"))),
        ("/edit #3 0.42 0.38", EditTrigger(3, Decimal("0.42"), Decimal("0.38"))),
        ("/cancel 3", CancelTrigger(3)),
        ("/cancel #12", CancelTrigger(12)),
        ("/sl@MyBot 7134921 0.40 0.35", SetStopLoss(ASSET, Decimal("0.40"), Decimal("0.35"))),
        ("/HEALTH", ShowHealth()),
        ("  /orders  ", ShowOrders()),
    ],
)
def test_parse_request_accepts_every_command_form(text, expected):
    assert parse_request(text) == expected


@pytest.mark.parametrize(
    ("text", "message_part"),
    [
        ("/price", "missing market"),
        ("/price will-trump-win-2028", "missing outcome after slug"),
        ("/open 7134921 20", "missing max_price"),
        ("/sl 7134921 0.40", "missing floor"),
        ("/sl 7134921 alert", "missing trigger"),
        ("/tp 7134921", "missing trigger"),
        ("/edit 3", "missing trigger"),
        ("/cancel", "missing id"),
        ("/health now", "unexpected argument now"),
        ("/price 7134921 extra", "unexpected argument extra"),
        ("/close 7134921 10 0.48 1", "unexpected argument 1"),
        ("/sl 7134921 0.40 0.35 50 alert extra", "unexpected argument"),
        ("/tp 7134921 0.80 0.78 25 5", "unexpected argument 5"),
        ("/cancel 3 4", "unexpected argument 4"),
        ("/open 7134921 abc 0.55", "usdc must be a number, got abc"),
        ("/open 7134921 0 0.55", "usdc must be a positive number"),
        ("/open 7134921 -5 0.55", "usdc must be a positive number"),
        ("/open 7134921 NaN 0.55", "usdc must be a positive number"),
        ("/open 7134921 Infinity 0.55", "usdc must be a positive number"),
        ("/open 7134921 20 1.2", "max_price must be < 1"),
        ("/sl 7134921 0.40 0,35", "floor must be a number"),
        ("/sl 7134921 0.40 0.35 foo", "shares must be a number, got foo"),
        ("/sl 7134921 1 0.35", "trigger must be < 1"),
        ("/close 7134921 10 2", "floor must be < 1"),
        ("/plan 7134921 0.70 0.65 1", "missing usdc"),
        ("/plan 7134921 0.70 0.75 1 100", "prices must satisfy 0 <= sl < entry < tp <= 1"),
        ("/plan 7134921 0.70 0.65 0.70 100", "prices must satisfy 0 <= sl < entry < tp <= 1"),
        ("/plan 7134921 0.70 0.65 1.2 100", "tp must be between 0 and 1"),
        ("/plan 7134921 0.70 NaN 1 100", "sl must be between 0 and 1"),
        ("/plan 7134921 abc 0.65 1 100", "entry must be a number, got abc"),
        ("/plan 7134921 0.70 0.65 1 0", "usdc must be a positive number"),
        ("/edit abc 0.42", "id must be a positive integer"),
        ("/edit #0 0.42", "id must be a positive integer"),
        ("/cancel 3.5", "id must be a positive integer"),
    ],
)
def test_parse_request_rejects_bad_arguments_with_usage(text, message_part):
    result = parse_request(text)

    assert isinstance(result, ParseError)
    assert message_part in result.message
    assert "Usage: /" in result.message


def test_parse_request_tp_size_without_floor_hints_at_positional_floor():
    result = parse_request("/tp m yes 0.8 50")

    assert isinstance(result, ParseError)
    assert "floor must be < 1 — to set size, pass the floor: /tp m yes 0.8 0.8 50" in result.message


def test_parse_request_numbers_are_decimal_not_float():
    result = parse_request("/open 7134921 20.10 0.55")

    assert isinstance(result.amount_usdc, Decimal)
    assert result.amount_usdc == Decimal("20.10")


@pytest.mark.parametrize("text", ["/unknown", "/start", "hello", ""])
def test_parse_request_unknown_command_lists_every_command(text):
    result = parse_request(text)

    assert isinstance(result, ParseError)
    for command in ("/health", "/price", "/positions", "/open", "/close", "/sl", "/tp", "/orders", "/edit", "/cancel", "/exit", "/help"):
        assert command in result.message
