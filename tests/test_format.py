from dataclasses import replace
from decimal import Decimal

from src.core import Position, TriggerKind, TriggerStatus, plan_trade
from src.format import (
    EMOJI_HEALTH,
    EMOJI_ORDERS,
    EMOJI_PLAN,
    EMOJI_POSITIONS,
    EMOJI_PRICE,
    EMOJI_STALE,
    render_health,
    render_help,
    render_orders,
    render_plan,
    render_positions,
    render_price,
)
from src.parse import COMMANDS, MarketRef, PlanTrade
from tests.helpers import ASSET_ID, T0_MS, make_book, make_market, make_trigger, running_state_with_position

OTHER_ASSET_ID = "987654321"


def make_state_with_triggers():
    return running_state_with_position(
        triggers=(
            make_trigger(trigger_id=1, trigger_price="0.45", floor_price="0.40"),
            make_trigger(
                trigger_id=2,
                kind=TriggerKind.TAKE_PROFIT,
                trigger_price="0.80",
                floor_price="0.78",
                size="50",
                status=TriggerStatus.PARTIAL,
                filled_shares=Decimal("20"),
            ),
            make_trigger(trigger_id=3, status=TriggerStatus.FILLED),
        )
    )


def test_render_health_shows_status_feed_ages_and_trigger_counts():
    state = replace(make_state_with_triggers(), started_at_ms=T0_MS - 3_600_000, stale_assets=frozenset({ASSET_ID}))

    text = render_health(state, now_ms=T0_MS + 45_000)

    assert text.startswith(EMOJI_HEALTH)
    assert "\n• Status: RUNNING\n" in text
    assert f"{EMOJI_STALE} stale: 1 assets" in text
    assert "Oldest data (live triggers): 45s" in text
    assert "Last data: 2023-11-14 22:13:20 UTC" in text
    assert "Uptime: 1:00:45" in text
    assert "• Triggers: 1 armed, 2 active, 3 total" in text


def test_render_positions_values_each_position_and_totals():
    state = make_state_with_triggers()
    state = replace(
        state,
        markets={**state.markets, OTHER_ASSET_ID: make_market(asset_id=OTHER_ASSET_ID, slug="will-y-happen")},
        positions={
            **state.positions,
            OTHER_ASSET_ID: Position(asset_id=OTHER_ASSET_ID, size=Decimal("10"), avg_price=Decimal("0.30")),
        },
    )

    text = render_positions(state, T0_MS + 45_000)

    assert text.startswith(EMOJI_POSITIONS)
    assert "\n• will-x-happen Yes\n  100 shares · avg 0.55 · mark 0.52" in text
    assert "value 52.00 · PnL -3.00 (-5.45%)" in text
    assert "triggers: #1 SL 0.45/0.40 all, #2 TP 0.80/0.78 50" in text
    assert "#3" not in text
    assert "10 shares · avg 0.30 · mark no bid" in text
    assert "mark 0.52 (45s old)" in text
    assert "value 0.00 · PnL -3.00 (-100.00%)" in text
    assert "triggers: none (unprotected)" in text
    assert "\n\nTotal value 52.00 · cost 58.00 · PnL -6.00 (-10.34%)" in text
    assert "USDC: 1000.00" in text


def test_render_orders_lists_active_triggers_with_remaining_and_distance():
    text = render_orders(make_state_with_triggers())

    assert text.startswith(EMOJI_ORDERS)
    assert "\n• #1 SL will-x-happen Yes · ARMED sell\n" in text
    assert "size all · remaining 100" in text
    assert "bid 0.52 · distance +13.46%" in text
    assert "#2 TP will-x-happen Yes · PARTIAL sell" in text
    assert "size 50 · remaining 30" in text
    assert "distance -53.85%" in text
    assert "#3" not in text


def test_render_price_shows_top_of_book_mid_spread_and_last_trade():
    text = render_price(make_market(), make_book("0.52", "0.54", best_bid_size="200", last_trade_price="0.53"))

    assert text.startswith(f"{EMOJI_PRICE} will-x-happen Yes")
    assert "\n• Bid 0.52 × 200\n" in text
    assert "Ask 0.54 × 1000" in text
    assert "• Mid 0.53 · spread 0.02" in text
    assert "• Last trade 0.53" in text

    one_sided = render_price(make_market(asset_id=OTHER_ASSET_ID, slug=""), make_book(None, "0.54"))

    assert one_sided.startswith(f"{EMOJI_PRICE} …654321")
    assert "Bid none" in one_sided
    assert "Mid n/a · spread n/a" in one_sided


def test_render_help_documents_every_command_and_the_configured_slippage():
    text = render_help(Decimal("0.03"))

    for usage, _ in COMMANDS.values():
        assert usage in text
    assert "floor of bid minus 0.03" in text


def plan_text(entry="0.70", stop_loss="0.65", take_profit="1", taker_fee_rate="0.04", book=None):
    market = make_market(taker_fee_rate=taker_fee_rate)
    request = PlanTrade(
        MarketRef(asset_id=ASSET_ID), Decimal(entry), Decimal(stop_loss), Decimal(take_profit), Decimal("100")
    )
    return render_plan(market, book or make_book("0.69", "0.71"), request, plan_trade(request, market))


def test_render_plan_shows_gross_and_net_table():
    assert plan_text() == "\n".join(
        [
            f"{EMOJI_PLAN} Plan will-x-happen Yes",
            "• Buy 142.857 shares at 0.70",
            "  for 100.00 USDC",
            "• SL 0.65 · TP 1 (resolution)",
            "• Now bid 0.69 · ask 0.71",
            "• Entry 0.70 is below ask 0.71:",
            "  a FAK buy would not fill now",
            "",
            "              Gross      Net",
            "Risk          -7.14    -9.64",
            "Risk %       -7.14%   -9.64%",
            "Benefit      +42.86   +41.66",
            "Benefit %   +42.86%  +41.66%",
            "R/R            6.00     4.32",
            "Break-even   14.29%   18.80%",
            "• Fees (rate 0.04): entry 1.20",
            "  SL exit 1.30 · TP exit 0.00",
        ]
    )


def test_render_plan_labels_no_stop_and_skips_the_warning_at_the_ask():
    text = plan_text(entry="0.71", stop_loss="0", take_profit="0.90")

    assert "• SL 0 (no stop) · TP 0.90\n" in text
    assert "would not fill" not in text


def test_render_plan_warns_without_ask():
    assert "• No ask: a FAK buy would not fill now" in plan_text(book=make_book("0.69", None))


def test_render_plan_shows_na_when_fees_exceed_the_benefit():
    text = plan_text(entry="0.50", stop_loss="0.45", take_profit="0.51", taker_fee_rate="0.07")

    assert "R/R            0.20      n/a" in text
    assert "Break-even   83.33%      n/a" in text
