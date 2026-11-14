from dataclasses import replace
from decimal import Decimal

from constants import DELAYED_ORDER_POLL_SECONDS, POSITIONS_FETCH_MAX_ATTEMPTS, RECONCILE_LOOKBACK_SECONDS
from src import format as fmt
from src.core import (
    BalanceFetchFailed,
    BalanceLoaded,
    BookUpdated,
    BotStatus,
    CheckFailed,
    CheckGeoblock,
    Exit,
    FatalErrorOccurred,
    FetchBalance,
    FetchBook,
    FetchPositions,
    FillsReconciled,
    GapPolicy,
    GeoblockChecked,
    MarketResolved,
    Notify,
    OrderDelayed,
    OrderFilled,
    OrderOutcomeUnknown,
    OrderPolled,
    OrderRejected,
    PendingOrder,
    PollOrder,
    Position,
    PositionsFetchFailed,
    PositionsLoaded,
    ReconcileFills,
    SendBuyOrder,
    SendSellOrder,
    Started,
    Subscribe,
    TickSizeChanged,
    Ticked,
    TriggerKind,
    TriggerStatus,
    UserRequestReceived,
    is_active,
    step,
    value_position,
)
from src.parse import (
    CancelTrigger,
    ClosePosition,
    EditTrigger,
    MarketRef,
    OpenPosition,
    SetStopLoss,
    SetTakeProfit,
    ShowHealth,
    ShowOrders,
    ShowPositions,
    ShowPrice,
    StopBot,
)
from tests.helpers import (
    ASSET_ID,
    CHAT_ID,
    T0_MS,
    make_book,
    make_config,
    make_market,
    make_state,
    make_trigger,
    run_events,
    running_state_with_position,
)

MARKET_REF = MarketRef(asset_id=ASSET_ID)


def of_type(commands, command_type):
    return [command for command in commands if isinstance(command, command_type)]


def texts(commands):
    return [command.text for command in of_type(commands, Notify)]


def book_at(best_bid, at_ms=T0_MS + 1_000, **kwargs):
    return BookUpdated(at_ms=at_ms, asset_id=ASSET_ID, book=make_book(best_bid, at_ms=at_ms, **kwargs))


def user_request(request, at_ms=T0_MS + 1_000, market=None, book=None):
    return UserRequestReceived(at_ms=at_ms, chat_id=CHAT_ID, request=request, market=market, book=book)


def fired_state(trigger=None, size="100"):
    """State right after the trigger's first sell went out as request r1."""
    trigger = trigger or make_trigger()
    state, _ = step(running_state_with_position(size=size, triggers=(trigger,)), book_at("0.45"))
    assert state.triggers[trigger.trigger_id].status == TriggerStatus.ORDER_PENDING
    return state


# STARTUP


def test_started_checks_geoblock():
    state, commands = step(make_state(), Started(at_ms=T0_MS))

    assert state.status == BotStatus.GEOBLOCK_CHECK
    assert state.started_at_ms == T0_MS
    assert commands == [CheckGeoblock()]


def test_geoblock_blocked_halts_with_exit_2():
    event = GeoblockChecked(at_ms=T0_MS, blocked=True, country="US")

    state, commands = step(make_state(status=BotStatus.GEOBLOCK_CHECK), event)

    assert state.status == BotStatus.HALTED
    assert commands == [Notify(None, "⚠️ Geoblocked (US) — exiting"), Exit(2)]


def test_geoblock_request_error_exits_1():
    event = GeoblockChecked(at_ms=T0_MS, blocked=False, country="", error="timeout")

    _, commands = step(make_state(status=BotStatus.GEOBLOCK_CHECK), event)

    assert commands == [Notify(None, "⚠️ Geoblock check failed: timeout — exiting"), Exit(1)]


def test_geoblock_allowed_fetches_positions():
    event = GeoblockChecked(at_ms=T0_MS, blocked=False, country="LU")

    state, commands = step(make_state(status=BotStatus.GEOBLOCK_CHECK), event)

    assert state.status == BotStatus.RECONCILING
    assert commands == [FetchPositions(None)]


def test_positions_fetch_failures_exit_1_after_max_attempts():
    state = make_state(status=BotStatus.RECONCILING)
    for _ in range(POSITIONS_FETCH_MAX_ATTEMPTS - 1):
        state, commands = step(state, PositionsFetchFailed(at_ms=T0_MS, error="502"))
        assert commands == []
        state, commands = step(state, Ticked(at_ms=T0_MS + 1000))
        assert commands == [FetchPositions(None)]

    state, commands = step(state, PositionsFetchFailed(at_ms=T0_MS, error="502"))

    assert state.positions_fetch_attempts == POSITIONS_FETCH_MAX_ATTEMPTS
    assert texts(commands)[0].startswith(fmt.EMOJI_FAILED)
    assert commands[-1] == Exit(1)


def test_positions_loaded_starts_running_and_lists_unprotected_positions():
    other_market = make_market(asset_id="456", slug="will-y-happen", outcome="No")
    event = PositionsLoaded(
        at_ms=T0_MS,
        positions=(
            Position(asset_id=ASSET_ID, size=Decimal("100"), avg_price=Decimal("0.55")),
            Position(asset_id="456", size=Decimal("20"), avg_price=Decimal("0.30")),
        ),
        markets=(make_market(), other_market),
        books=((ASSET_ID, make_book("0.52")), ("456", make_book("0.30"))),
        collateral_usdc=Decimal("250"),
    )

    state, commands = step(make_state(status=BotStatus.RECONCILING), event)

    assert state.status == BotStatus.RUNNING
    assert state.collateral_usdc == Decimal("250")
    assert state.subscribed_assets == {ASSET_ID, "456"}
    assert of_type(commands, Subscribe) == [Subscribe((ASSET_ID, "456"))]
    [text] = texts(commands)
    assert text.startswith(
        f"{fmt.EMOJI_STARTED} Bot started — trigger orders are not persisted. Unprotected positions:"
    )
    assert "will-x-happen Yes" in text and "will-y-happen No" in text


def test_fatal_error_halts_with_exit_2():
    state, commands = step(running_state_with_position(), FatalErrorOccurred(at_ms=T0_MS, reason="401 auth"))

    assert state.status == BotStatus.HALTED
    assert commands == [Notify(None, "🛑 Halted: 401 auth"), Exit(2)]


def test_halted_bot_ignores_books():
    state = running_state_with_position(triggers=(make_trigger(),), status=BotStatus.HALTED)

    _, commands = step(state, book_at("0.40"))

    assert commands == []


# BOOK UPDATES AND FIRING


def test_stop_loss_fires_at_bid_equal_to_trigger():
    state = running_state_with_position(triggers=(make_trigger(trigger_price="0.45", floor_price="0.40"),))

    state, commands = step(state, book_at("0.45"))

    assert state.triggers[1].status == TriggerStatus.ORDER_PENDING
    assert of_type(commands, SendSellOrder) == [SendSellOrder("r1", ASSET_ID, Decimal("100"), Decimal("0.40"))]
    assert state.pending_orders == {
        "r1": PendingOrder("r1", ASSET_ID, "SELL", sent_at_ms=T0_MS + 1_000, trigger_id=1, reply_chat_id=None)
    }
    assert state.next_request_id == 2
    triggered_text, placed_text = texts(commands)
    assert triggered_text.startswith(fmt.EMOJI_TRIGGERED)
    assert placed_text.startswith(fmt.EMOJI_PLACED)


def test_take_profit_fires_at_bid_equal_to_trigger():
    trigger = make_trigger(kind=TriggerKind.TAKE_PROFIT, trigger_price="0.60", floor_price="0.60")
    state = running_state_with_position(triggers=(trigger,))

    state, commands = step(state, book_at("0.60"))

    assert state.triggers[1].status == TriggerStatus.ORDER_PENDING
    assert of_type(commands, SendSellOrder) == [SendSellOrder("r1", ASSET_ID, Decimal("100"), Decimal("0.60"))]


def test_triggers_do_not_fire_one_tick_away():
    stop_loss = make_trigger(trigger_id=1, trigger_price="0.45", floor_price="0.40")
    take_profit = make_trigger(trigger_id=2, kind=TriggerKind.TAKE_PROFIT, trigger_price="0.60", floor_price="0.60")
    state = running_state_with_position(triggers=(stop_loss, take_profit))

    state, commands = run_events(state, book_at("0.46"), book_at("0.59"))

    assert commands == []
    assert {trigger.status for trigger in state.triggers.values()} == {TriggerStatus.ARMED}


def test_missing_bid_never_fires():
    state = running_state_with_position(triggers=(make_trigger(status=TriggerStatus.TRIGGERED),))

    state, commands = step(state, book_at(None, best_ask="0.10"))

    assert commands == []
    assert state.triggers[1].status == TriggerStatus.TRIGGERED


def test_waiting_below_floor_notifies_once_then_sells_at_floor():
    state = running_state_with_position(triggers=(make_trigger(trigger_price="0.45", floor_price="0.40"),))

    state, first_commands = step(state, book_at("0.38"))
    state, second_commands = step(state, book_at("0.37"))
    state, third_commands = step(state, book_at("0.40"))

    assert state.triggers[1].status == TriggerStatus.ORDER_PENDING
    assert [text[0] for text in texts(first_commands)] == [fmt.EMOJI_TRIGGERED, fmt.EMOJI_WAITING]
    assert texts(first_commands)[1] == "⏳ SL #1 will-x-happen Yes: bid 0.38 < floor 0.40, waiting"
    assert second_commands == []
    assert of_type(third_commands, SendSellOrder) == [SendSellOrder("r1", ASSET_ID, Decimal("100"), Decimal("0.40"))]


def test_one_sell_in_flight_per_asset_across_two_triggers():
    first = make_trigger(trigger_id=1, trigger_price="0.45", size="30")
    second = make_trigger(trigger_id=2, trigger_price="0.46", size="30")
    state = running_state_with_position(triggers=(first, second))

    state, commands = step(state, book_at("0.45"))

    assert of_type(commands, SendSellOrder) == [SendSellOrder("r1", ASSET_ID, Decimal("30"), Decimal("0.40"))]
    assert state.triggers[2].status == TriggerStatus.TRIGGERED

    state, commands = run_events(
        state,
        OrderFilled(T0_MS + 2_000, "r1", Decimal("30"), Decimal("13.5"), "order-1", ("trade-1",)),
        book_at("0.44", at_ms=T0_MS + 3_000),
    )

    assert state.triggers[1].status == TriggerStatus.FILLED
    assert of_type(commands, SendSellOrder) == [SendSellOrder("r2", ASSET_ID, Decimal("30"), Decimal("0.40"))]


def test_triggered_below_min_order_size_fails():
    state = running_state_with_position(size="3", triggers=(make_trigger(status=TriggerStatus.TRIGGERED),))

    state, commands = step(state, book_at("0.44"))

    assert state.triggers[1].status == TriggerStatus.FAILED
    assert state.triggers[1].close_reason == "below_min_size"
    assert of_type(commands, SendSellOrder) == []
    assert texts(commands)[0].startswith(fmt.EMOJI_FAILED)


def test_partial_with_dust_remainder_is_closed():
    trigger = make_trigger(size="50", status=TriggerStatus.PARTIAL, filled_shares=Decimal("47"))
    state = running_state_with_position(triggers=(trigger,))

    state, commands = step(state, book_at("0.44"))

    assert state.triggers[1].status == TriggerStatus.PARTIAL
    assert state.triggers[1].close_reason == "dust"
    assert not is_active(state.triggers[1])
    assert of_type(commands, SendSellOrder) == []
    assert texts(commands)[0].startswith(fmt.EMOJI_CANCELLED)


def test_send_rounds_floor_up_to_current_tick():
    trigger = make_trigger(trigger_price="0.455", floor_price="0.405")
    state = running_state_with_position(triggers=(trigger,))

    state, commands = run_events(state, TickSizeChanged(T0_MS, ASSET_ID, Decimal("0.01")), book_at("0.45"))

    assert of_type(commands, SendSellOrder)[0].floor_price == Decimal("0.41")


def test_tick_size_change_updates_market():
    state, commands = step(running_state_with_position(), TickSizeChanged(T0_MS, ASSET_ID, Decimal("0.001")))

    assert state.markets[ASSET_ID].tick_size == Decimal("0.001")
    assert commands == []


# ORDER RESULTS


def test_full_fill_completes_trigger_and_fetches_balance():
    state = fired_state()

    state, commands = step(state, OrderFilled(T0_MS + 2_000, "r1", Decimal("100"), Decimal("45"), "order-1", ("t1",)))

    trigger = state.triggers[1]
    assert trigger.status == TriggerStatus.FILLED
    assert trigger.filled_shares == Decimal("100")
    assert trigger.proceeds_usdc == Decimal("45")
    assert trigger.credited_trade_ids == {"t1"}
    assert state.pending_orders == {}
    assert ASSET_ID not in state.positions
    assert state.collateral_usdc == Decimal("1045")
    assert texts(commands) == ["✅ SL #1 will-x-happen Yes: sold 100 @ 0.45 for 45 USDC"]
    assert of_type(commands, FetchBalance) == [FetchBalance(ASSET_ID)]


def test_partial_fill_refires_remainder_on_next_qualifying_book():
    state = fired_state()

    state, commands = step(state, OrderFilled(T0_MS + 2_000, "r1", Decimal("60"), Decimal("27"), "order-1", ("t1",)))

    assert state.triggers[1].status == TriggerStatus.PARTIAL
    assert state.positions[ASSET_ID].size == Decimal("40")
    assert texts(commands)[0].startswith(fmt.EMOJI_PARTIAL)

    state, commands = step(state, book_at("0.44", at_ms=T0_MS + 3_000))

    assert of_type(commands, SendSellOrder) == [SendSellOrder("r2", ASSET_ID, Decimal("40"), Decimal("0.40"))]
    assert state.triggers[1].status == TriggerStatus.ORDER_PENDING


def test_fak_not_filled_returns_to_triggered_and_retries_on_next_book():
    state = fired_state()

    state, commands = step(state, OrderRejected(T0_MS + 2_000, "r1", "fak_not_filled"))

    assert state.triggers[1].status == TriggerStatus.TRIGGERED
    assert state.pending_orders == {}
    assert commands == []

    _, commands = step(state, book_at("0.44", at_ms=T0_MS + 3_000))

    assert len(of_type(commands, SendSellOrder)) == 1


def test_fak_not_filled_after_partial_returns_to_partial():
    state = fired_state()
    state, _ = run_events(
        state,
        OrderFilled(T0_MS + 2_000, "r1", Decimal("60"), Decimal("27"), "order-1"),
        book_at("0.44", at_ms=T0_MS + 3_000),
        OrderRejected(T0_MS + 4_000, "r2", "fak_not_filled"),
    )

    assert state.triggers[1].status == TriggerStatus.PARTIAL


def test_not_enough_balance_returns_to_triggered_and_fetches_balance():
    state = fired_state()

    state, commands = step(state, OrderRejected(T0_MS + 2_000, "r1", "not_enough_balance"))

    assert state.triggers[1].status == TriggerStatus.TRIGGERED
    assert commands == [FetchBalance(ASSET_ID)]


def test_terminal_rejection_fails_trigger():
    state = fired_state()

    state, commands = step(state, OrderRejected(T0_MS + 2_000, "r1", "market_not_ready"))

    assert state.triggers[1].status == TriggerStatus.FAILED
    assert state.triggers[1].close_reason == "market_not_ready"
    assert texts(commands)[0].startswith(fmt.EMOJI_FAILED)

    _, commands = step(state, book_at("0.44", at_ms=T0_MS + 3_000))

    assert commands == []


def test_delayed_order_polls_on_cadence():
    poll_ms = DELAYED_ORDER_POLL_SECONDS * 1_000
    delayed_at_ms = T0_MS + 2_000
    state, _ = step(fired_state(), OrderDelayed(delayed_at_ms, "r1", "order-1"))

    assert state.pending_orders["r1"].order_id == "order-1"

    poll_times = [delayed_at_ms + offset for offset in (poll_ms - 1, poll_ms, poll_ms + 1, 2 * poll_ms)]
    polls = []
    for at_ms in poll_times:
        state, commands = step(state, Ticked(at_ms))
        polls.append(of_type(commands, PollOrder))

    assert polls == [[], [PollOrder("r1", "order-1")], [], [PollOrder("r1", "order-1")]]


def test_final_poll_settles_like_a_fill():
    state, _ = step(fired_state(), OrderDelayed(T0_MS + 2_000, "r1", "order-1"))

    state, commands = step(state, OrderPolled(T0_MS + 4_000, "r1", Decimal("100"), Decimal("45"), is_final=True))

    assert state.triggers[1].status == TriggerStatus.FILLED
    assert of_type(commands, FetchBalance) == [FetchBalance(ASSET_ID)]


def test_final_poll_with_zero_fill_returns_to_triggered():
    state, _ = step(fired_state(), OrderDelayed(T0_MS + 2_000, "r1", "order-1"))

    state, _ = step(state, OrderPolled(T0_MS + 4_000, "r1", Decimal(0), Decimal(0), is_final=True))

    assert state.triggers[1].status == TriggerStatus.TRIGGERED
    assert state.pending_orders == {}


def test_non_final_poll_keeps_order_pending():
    state, _ = step(fired_state(), OrderDelayed(T0_MS + 2_000, "r1", "order-1"))

    state, commands = step(state, OrderPolled(T0_MS + 4_000, "r1", Decimal(0), Decimal(0), is_final=False))

    assert commands == []
    assert "r1" in state.pending_orders


def test_outcome_unknown_reconciles_and_never_resends():
    state = fired_state()

    state, commands = step(state, OrderOutcomeUnknown(T0_MS + 11_000, "r1", "timeout"))

    since_ms = T0_MS + 1_000 - RECONCILE_LOOKBACK_SECONDS * 1_000
    assert commands == [ReconcileFills("r1", ASSET_ID, "SELL", since_ms=since_ms, exclude_trade_ids=frozenset())]
    assert state.pending_orders["r1"].outcome_unknown

    later_events = []
    for second in range(12, 60):
        later_events += [Ticked(T0_MS + second * 1_000), book_at("0.41", at_ms=T0_MS + second * 1_000)]
    state, commands = run_events(state, *later_events)

    assert of_type(commands, SendSellOrder) == []
    assert of_type(commands, ReconcileFills) == []
    assert state.triggers[1].status == TriggerStatus.ORDER_PENDING


def test_check_failed_rechecks_on_next_tick():
    state, _ = step(fired_state(), OrderOutcomeUnknown(T0_MS + 11_000, "r1", "timeout"))

    state, commands = run_events(state, CheckFailed(T0_MS + 12_000, "r1", "timeout"), Ticked(T0_MS + 12_500))

    assert of_type(commands, ReconcileFills) == [
        ReconcileFills("r1", ASSET_ID, "SELL", since_ms=T0_MS - 4_000, exclude_trade_ids=frozenset())
    ]

    _, commands = step(state, Ticked(T0_MS + 13_500))

    assert of_type(commands, ReconcileFills) == []


def test_fills_reconciled_settles_like_a_fill():
    state, _ = step(fired_state(), OrderOutcomeUnknown(T0_MS + 11_000, "r1", "timeout"))

    state, commands = step(state, FillsReconciled(T0_MS + 12_000, "r1", Decimal("100"), Decimal("45"), ("t1", "t2")))

    assert state.triggers[1].status == TriggerStatus.FILLED
    assert state.triggers[1].credited_trade_ids == {"t1", "t2"}
    assert of_type(commands, FetchBalance) == [FetchBalance(ASSET_ID)]


def test_reconcile_excludes_trade_ids_already_credited():
    state = fired_state()
    state, _ = run_events(
        state,
        OrderFilled(T0_MS + 2_000, "r1", Decimal("60"), Decimal("27"), "order-1", ("t1",)),
        book_at("0.44", at_ms=T0_MS + 3_000),
    )

    _, commands = step(state, OrderOutcomeUnknown(T0_MS + 13_000, "r2", "timeout"))

    assert commands[0].exclude_trade_ids == {"t1"}


def test_results_for_unknown_request_ids_are_ignored():
    state = fired_state()

    events = (
        OrderFilled(T0_MS, "r9", Decimal("1"), Decimal("1"), "order-9"),
        OrderRejected(T0_MS, "r9", "unknown"),
        OrderDelayed(T0_MS, "r9", "order-9"),
        OrderOutcomeUnknown(T0_MS, "r9", "timeout"),
        CheckFailed(T0_MS, "r9", "timeout"),
    )
    new_state, commands = run_events(state, *events)

    assert commands == []
    assert new_state == state


# SIBLINGS


def test_balance_resizes_sibling_with_explicit_size():
    state = running_state_with_position(triggers=(make_trigger(size="80"),))

    state, commands = step(state, BalanceLoaded(T0_MS, ASSET_ID, Decimal("50")))

    assert state.positions[ASSET_ID] == Position(ASSET_ID, Decimal("50"), Decimal("0.55"))
    assert state.triggers[1].size == Decimal("50")
    assert texts(commands) == ["🔧 SL #1 will-x-happen Yes resized to 50 shares (position 50)"]


def test_balance_resize_keeps_already_filled_shares():
    trigger = make_trigger(size="80", status=TriggerStatus.PARTIAL, filled_shares=Decimal("30"))
    state = running_state_with_position(triggers=(trigger,))

    state, _ = step(state, BalanceLoaded(T0_MS, ASSET_ID, Decimal("20")))

    assert state.triggers[1].size == Decimal("50")


def test_balance_below_min_order_size_cancels_siblings():
    trigger = make_trigger(trigger_id=1)
    filled = make_trigger(trigger_id=2, status=TriggerStatus.FILLED)
    state = running_state_with_position(triggers=(trigger, filled))

    state, commands = step(state, BalanceLoaded(T0_MS, ASSET_ID, Decimal("2")))

    assert state.triggers[1].status == TriggerStatus.CANCELLED
    assert state.triggers[1].close_reason == "position_closed"
    assert state.triggers[2].status == TriggerStatus.FILLED
    assert [text[0] for text in texts(commands)] == [fmt.EMOJI_CANCELLED]


def test_balance_fetch_failed_is_ignored():
    state = running_state_with_position(triggers=(make_trigger(),))

    new_state, commands = step(state, BalanceFetchFailed(T0_MS, ASSET_ID, "timeout"))

    assert (new_state, commands) == (state, [])


# STALENESS AND HEARTBEAT


def test_stale_data_enters_and_leaves_stale():
    state = running_state_with_position(triggers=(make_trigger(),))

    state, commands = step(state, Ticked(T0_MS + 30_001))

    assert state.status == BotStatus.STALE
    assert state.stale_assets == {ASSET_ID}
    assert texts(commands) == ["🟠 Stale data: will-x-happen Yes"]

    state, commands = step(state, book_at("0.50", at_ms=T0_MS + 31_000))

    assert state.status == BotStatus.RUNNING
    assert state.stale_assets == frozenset()
    assert texts(commands) == ["🟢 Data fresh again"]


def test_stale_probe_cadence():
    state = running_state_with_position(triggers=(make_trigger(),))
    probes = []
    for at_ms in (T0_MS + 15_000, T0_MS + 15_001, T0_MS + 16_000, T0_MS + 30_001):
        state, commands = step(state, Ticked(at_ms))
        probes.append(of_type(commands, FetchBook))

    assert probes == [[], [FetchBook(ASSET_ID)], [], [FetchBook(ASSET_ID)]]

    state, _ = step(state, book_at("0.50", at_ms=T0_MS + 30_500))

    assert ASSET_ID not in state.probe_sent_at_ms


def test_assets_without_live_triggers_are_not_watched():
    state = running_state_with_position(triggers=(make_trigger(status=TriggerStatus.FILLED),))

    state, commands = step(state, Ticked(T0_MS + 60_000))

    assert commands == []
    assert state.status == BotStatus.RUNNING


def test_stale_asset_without_live_triggers_stops_counting():
    state = running_state_with_position(triggers=(make_trigger(),))
    state, _ = step(state, Ticked(T0_MS + 30_001))
    state = replace(state, triggers={1: replace(state.triggers[1], status=TriggerStatus.CANCELLED)})

    state, commands = step(state, Ticked(T0_MS + 31_000))

    assert state.status == BotStatus.RUNNING
    assert texts(commands) == ["🟢 Data fresh again"]


def test_gap_through_trigger_after_stale_sells_with_policy_sell():
    state = running_state_with_position(triggers=(make_trigger(trigger_price="0.45", floor_price="0.30"),))
    state, _ = step(state, Ticked(T0_MS + 30_001))

    state, commands = step(state, book_at("0.35", at_ms=T0_MS + 31_000))

    assert state.status == BotStatus.RUNNING
    assert state.triggers[1].status == TriggerStatus.ORDER_PENDING
    assert of_type(commands, SendSellOrder) == [SendSellOrder("r1", ASSET_ID, Decimal("100"), Decimal("0.30"))]


def test_gap_through_trigger_after_stale_cancels_with_policy_alert():
    trigger = make_trigger(trigger_price="0.45", floor_price="0.30", policy=GapPolicy.ALERT)
    state = running_state_with_position(triggers=(trigger,))
    state, _ = step(state, Ticked(T0_MS + 30_001))

    state, commands = step(state, book_at("0.35", at_ms=T0_MS + 31_000))

    assert state.triggers[1].status == TriggerStatus.CANCELLED
    assert state.triggers[1].close_reason == "gap_alert"
    assert of_type(commands, SendSellOrder) == []
    assert "⚠️ SL #1 will-x-happen Yes gapped: bid 0.35 vs trigger 0.45 — not selling. /close or re-arm" in texts(
        commands
    )


def test_alert_policy_without_stale_gap_fires_normally():
    trigger = make_trigger(trigger_price="0.45", floor_price="0.30", policy=GapPolicy.ALERT)
    state = running_state_with_position(triggers=(trigger,))

    state, commands = step(state, book_at("0.35"))

    assert len(of_type(commands, SendSellOrder)) == 1


def test_heartbeat_cadence():
    hour_ms = 3_600_000
    state = running_state_with_position(config=make_config(heartbeat_ms=hour_ms))
    heartbeats = []
    for at_ms in (T0_MS + hour_ms - 1, T0_MS + hour_ms, T0_MS + hour_ms + 1_000, T0_MS + 2 * hour_ms):
        state, commands = step(state, Ticked(at_ms))
        heartbeats.append(len([text for text in texts(commands) if text.startswith(fmt.EMOJI_HEARTBEAT)]))

    assert heartbeats == [0, 1, 0, 1]


def test_heartbeat_off_by_default():
    _, commands = step(running_state_with_position(), Ticked(T0_MS + 10 * 3_600_000))

    assert commands == []


# MARKET LIFECYCLE


def test_market_resolved_cancels_live_triggers():
    triggers = (
        make_trigger(trigger_id=1),
        make_trigger(trigger_id=2, kind=TriggerKind.TAKE_PROFIT, trigger_price="0.60", floor_price="0.60"),
        make_trigger(trigger_id=3, status=TriggerStatus.FILLED),
    )
    state = running_state_with_position(triggers=triggers)

    state, commands = step(state, MarketResolved(T0_MS, (ASSET_ID, "456"), winning_asset_id="456"))

    assert [trigger.status for trigger in state.triggers.values()] == [
        TriggerStatus.CANCELLED,
        TriggerStatus.CANCELLED,
        TriggerStatus.FILLED,
    ]
    assert state.triggers[1].close_reason == "market_resolved"
    assert texts(commands)[0] == "🚫 SL #1 cancelled: market resolved — will-x-happen Yes lost"
    assert len(texts(commands)) == 2


# USER REQUESTS


def market_request(request, best_bid="0.52", market=None, at_ms=T0_MS + 1_000):
    return user_request(request, at_ms=at_ms, market=market or make_market(), book=make_book(best_bid, at_ms=at_ms))


def test_requests_while_starting_only_health_answers():
    state = make_state(status=BotStatus.RECONCILING)

    _, health_commands = step(state, user_request(ShowHealth()))
    _, orders_commands = step(state, user_request(ShowOrders()))

    assert health_commands[0].chat_id == CHAT_ID
    assert texts(health_commands)[0].startswith(fmt.EMOJI_HEALTH)
    assert orders_commands == [Notify(CHAT_ID, "❌ Bot is starting, retry in a moment")]


def test_request_caches_market_keeps_fresher_book_and_subscribes_once():
    market = make_market(asset_id="456", slug="will-y-happen", outcome="No")
    request = ShowPrice(MarketRef(asset_id="456"))
    state = running_state_with_position()

    state, commands = step(state, market_request(request, best_bid="0.30", market=market))

    assert state.markets["456"] == market
    assert state.books["456"].best_bid == Decimal("0.30")
    assert state.subscribed_assets == {ASSET_ID, "456"}
    assert of_type(commands, Subscribe) == [Subscribe(("456",))]
    assert texts(commands)[0].startswith(fmt.EMOJI_PRICE)

    state, commands = step(state, market_request(request, best_bid="0.10", market=market, at_ms=T0_MS))

    assert state.books["456"].best_bid == Decimal("0.30")
    assert of_type(commands, Subscribe) == []


def test_show_requests_reply_with_rendered_text():
    state = running_state_with_position()

    _, health_commands = step(state, user_request(ShowHealth()))
    _, orders_commands = step(state, user_request(ShowOrders()))
    _, price_commands = step(state, market_request(ShowPrice(MARKET_REF)))

    for commands, emoji in (
        (health_commands, fmt.EMOJI_HEALTH),
        (orders_commands, fmt.EMOJI_ORDERS),
        (price_commands, fmt.EMOJI_PRICE),
    ):
        [notify] = of_type(commands, Notify)
        assert notify.chat_id == CHAT_ID
        assert notify.text.startswith(emoji)


def test_show_positions_fetches_then_replies_to_the_requesting_chat():
    state = running_state_with_position()

    state, commands = step(state, user_request(ShowPositions()))

    assert commands == [FetchPositions(CHAT_ID)]

    loaded = PositionsLoaded(
        at_ms=T0_MS + 2_000,
        positions=(Position(ASSET_ID, Decimal("100"), Decimal("0.55")),),
        markets=(make_market(),),
        books=((ASSET_ID, make_book("0.53", at_ms=T0_MS + 2_000)),),
        collateral_usdc=Decimal("900"),
        reply_chat_id=CHAT_ID,
    )
    state, commands = step(state, loaded)

    assert state.status == BotStatus.RUNNING
    assert state.collateral_usdc == Decimal("900")
    assert state.books[ASSET_ID].best_bid == Decimal("0.53")
    [notify] = commands
    assert notify.chat_id == CHAT_ID
    assert notify.text.startswith(fmt.EMOJI_POSITIONS)


def test_positions_refresh_keeps_positions_missing_from_the_fetch():
    state = running_state_with_position()
    loaded = PositionsLoaded(
        T0_MS, positions=(), markets=(), books=(), collateral_usdc=Decimal("900"), reply_chat_id=CHAT_ID
    )

    state, _ = step(state, loaded)

    assert state.positions[ASSET_ID].size == Decimal("100")


def test_positions_fetch_failure_while_running_replies():
    _, commands = step(running_state_with_position(), PositionsFetchFailed(T0_MS, "502", reply_chat_id=CHAT_ID))

    assert commands == [Notify(CHAT_ID, "❌ Positions fetch failed: 502")]


def test_set_stop_loss_arms_trigger_with_sell_policy_by_default():
    state = running_state_with_position()

    state, commands = step(state, market_request(SetStopLoss(MARKET_REF, Decimal("0.45"), Decimal("0.40"))))

    assert state.triggers[1] == make_trigger(trigger_id=1, trigger_price="0.45", floor_price="0.40")
    assert state.next_trigger_id == 2
    [notify] = commands
    assert notify.chat_id == CHAT_ID
    assert notify.text.startswith(f"{fmt.EMOJI_ARMED} SL #1 armed")


def test_set_stop_loss_with_size_and_alert_policy():
    request = SetStopLoss(MARKET_REF, Decimal("0.45"), Decimal("0.40"), size=Decimal("50"), policy="alert")

    state, _ = step(running_state_with_position(), market_request(request))

    assert state.triggers[1].size == Decimal("50")
    assert state.triggers[1].policy == GapPolicy.ALERT


def test_set_take_profit_floor_defaults_to_trigger():
    state, _ = step(running_state_with_position(), market_request(SetTakeProfit(MARKET_REF, Decimal("0.80"))))

    trigger = state.triggers[1]
    assert trigger.kind == TriggerKind.TAKE_PROFIT
    assert trigger.floor_price == Decimal("0.80")
    assert trigger.policy == GapPolicy.SELL


def test_set_trigger_rejected_when_already_through():
    state = running_state_with_position()

    _, stop_loss_commands = step(
        state, market_request(SetStopLoss(MARKET_REF, Decimal("0.45"), Decimal("0.40")), best_bid="0.42")
    )
    _, at_trigger_commands = step(
        state, market_request(SetStopLoss(MARKET_REF, Decimal("0.45"), Decimal("0.40")), best_bid="0.45")
    )
    _, take_profit_commands = step(state, market_request(SetTakeProfit(MARKET_REF, Decimal("0.80")), best_bid="0.82"))

    assert stop_loss_commands == [Notify(CHAT_ID, "❌ bid 0.42 is already at or below trigger 0.45")]
    assert texts(at_trigger_commands)[0].startswith(fmt.EMOJI_FAILED)
    assert take_profit_commands == [Notify(CHAT_ID, "❌ bid 0.82 is already at or above trigger 0.80")]


def test_set_trigger_rejects_prices_off_the_tick_grid():
    new_state, commands = step(
        running_state_with_position(), market_request(SetStopLoss(MARKET_REF, Decimal("0.455"), Decimal("0.40")))
    )

    assert new_state.triggers == {}
    assert commands == [Notify(CHAT_ID, "❌ prices must be multiples of the tick size 0.01")]


def test_set_trigger_rejects_invalid_price_order():
    state = running_state_with_position()
    requests = (
        SetStopLoss(MARKET_REF, Decimal("0.40"), Decimal("0.45")),
        SetTakeProfit(MARKET_REF, Decimal("1"), Decimal("0.90")),
        SetStopLoss(MARKET_REF, Decimal("0.45"), Decimal("0")),
    )

    for request in requests:
        new_state, commands = step(state, market_request(request))
        assert new_state.triggers == {}
        assert texts(commands)[0].startswith(fmt.EMOJI_FAILED)


def test_set_trigger_rejects_position_or_size_below_min_order_size():
    request = SetStopLoss(MARKET_REF, Decimal("0.45"), Decimal("0.40"))
    small_position = running_state_with_position(size="3")
    small_size = SetStopLoss(MARKET_REF, Decimal("0.45"), Decimal("0.40"), size=Decimal("4"))

    _, position_commands = step(small_position, market_request(request))
    _, size_commands = step(running_state_with_position(), market_request(small_size))

    assert position_commands == [Notify(CHAT_ID, "❌ position 3 is below the min order size 5")]
    assert size_commands == [Notify(CHAT_ID, "❌ size 4 is below the min order size 5")]


def test_edit_trigger_only_while_armed():
    state = running_state_with_position(triggers=(make_trigger(status=TriggerStatus.TRIGGERED),))

    new_state, commands = step(state, user_request(EditTrigger(1, Decimal("0.42"))))

    assert new_state == state
    assert commands == [Notify(CHAT_ID, "❌ #1 is TRIGGERED, only ARMED triggers can be edited")]


def test_edit_trigger_floor_defaults():
    stop_loss = make_trigger(trigger_id=1, trigger_price="0.45", floor_price="0.40")
    take_profit = make_trigger(trigger_id=2, kind=TriggerKind.TAKE_PROFIT, trigger_price="0.80", floor_price="0.78")
    state = running_state_with_position(triggers=(stop_loss, take_profit))

    state, commands = run_events(
        state, user_request(EditTrigger(1, Decimal("0.42"))), user_request(EditTrigger(2, Decimal("0.70")))
    )

    assert (state.triggers[1].trigger_price, state.triggers[1].floor_price) == (Decimal("0.42"), Decimal("0.40"))
    assert (state.triggers[2].trigger_price, state.triggers[2].floor_price) == (Decimal("0.70"), Decimal("0.70"))
    assert all(text.startswith(fmt.EMOJI_ARMED) for text in texts(commands))

    state, _ = step(state, user_request(EditTrigger(1, Decimal("0.43"), Decimal("0.35"))))

    assert state.triggers[1].floor_price == Decimal("0.35")


def test_edit_trigger_rejected_when_already_through():
    state = running_state_with_position(triggers=(make_trigger(trigger_price="0.45", floor_price="0.40"),))

    new_state, commands = step(state, user_request(EditTrigger(1, Decimal("0.52"))))

    assert new_state.triggers == state.triggers
    assert commands == [Notify(CHAT_ID, "❌ bid 0.52 is already at or below trigger 0.52")]


def test_edit_or_cancel_unknown_trigger_is_refused():
    state = running_state_with_position()

    _, edit_commands = step(state, user_request(EditTrigger(7, Decimal("0.42"))))
    _, cancel_commands = step(state, user_request(CancelTrigger(7)))

    assert edit_commands == cancel_commands == [Notify(CHAT_ID, "❌ #7 not found")]


def test_cancel_trigger_refused_while_order_pending():
    state = fired_state()

    new_state, commands = step(state, user_request(CancelTrigger(1)))

    assert new_state.triggers == state.triggers
    assert commands == [Notify(CHAT_ID, "❌ #1 has an order in flight, retry in a moment")]


def test_cancel_trigger_cancels_live_trigger():
    state = running_state_with_position(
        triggers=(make_trigger(status=TriggerStatus.PARTIAL, filled_shares=Decimal("10")),)
    )

    state, commands = step(state, user_request(CancelTrigger(1)))

    assert state.triggers[1].status == TriggerStatus.CANCELLED
    assert state.triggers[1].close_reason == "user"
    assert commands == [Notify(CHAT_ID, "🚫 SL #1 will-x-happen Yes cancelled")]


def test_cancel_closed_trigger_is_refused():
    state = running_state_with_position(triggers=(make_trigger(status=TriggerStatus.FILLED),))

    _, commands = step(state, user_request(CancelTrigger(1)))

    assert commands == [Notify(CHAT_ID, "❌ #1 is already closed (FILLED)")]


def test_open_position_rounds_max_price_down_and_averages_the_fill():
    state = running_state_with_position(size="100", avg_price="0.55")

    state, commands = step(state, market_request(OpenPosition(MARKET_REF, Decimal("20"), Decimal("0.567"))))

    assert of_type(commands, SendBuyOrder) == [SendBuyOrder("r1", ASSET_ID, Decimal("20"), Decimal("0.56"))]
    assert state.pending_orders["r1"].side == "BUY"
    assert state.pending_orders["r1"].reply_chat_id == CHAT_ID

    state, commands = step(state, OrderFilled(T0_MS + 2_000, "r1", Decimal("40"), Decimal("20"), "order-1"))

    assert state.positions[ASSET_ID] == Position(ASSET_ID, Decimal("140"), Decimal("75") / Decimal("140"))
    assert state.collateral_usdc == Decimal("980")
    assert commands == [Notify(CHAT_ID, "✅ /open will-x-happen Yes: bought 40 @ 0.5 for 20 USDC")]


def test_exit_lists_dropped_triggers_and_exits_0():
    triggers = (make_trigger(), make_trigger(trigger_id=2, status=TriggerStatus.FILLED))

    _, commands = step(running_state_with_position(triggers=triggers), user_request(StopBot()))

    assert commands == [
        Notify(None, "👋 Exiting on /exit — trigger orders dropped: 1\n#1 SL 0.45/0.40 all will-x-happen Yes"),
        Exit(0),
    ]


def test_exit_works_while_starting():
    _, commands = step(make_state(status=BotStatus.RECONCILING), user_request(StopBot()))

    assert commands == [Notify(None, "👋 Exiting on /exit — trigger orders dropped: 0"), Exit(0)]


def test_exit_refused_while_order_in_flight():
    _, commands = step(fired_state(), user_request(StopBot()))

    assert commands == [Notify(CHAT_ID, "❌ an order is in flight, retry /exit in a moment")]


def test_open_position_below_min_order_size_is_refused():
    state = running_state_with_position()

    refused_state, commands = step(state, market_request(OpenPosition(MARKET_REF, Decimal("1"), Decimal("0.70"))))

    assert commands == [
        Notify(
            CHAT_ID,
            "❌ /open will-x-happen Yes: min order size is 5 shares, at max price 0.70 that needs at least 3.50 USDC",
        )
    ]
    assert refused_state.pending_orders == {}

    _, commands = step(state, market_request(OpenPosition(MARKET_REF, Decimal("3.50"), Decimal("0.70"))))

    assert of_type(commands, SendBuyOrder) == [SendBuyOrder("r1", ASSET_ID, Decimal("3.50"), Decimal("0.70"))]


def test_open_position_zero_fill_is_reported_not_retried():
    state, _ = step(
        running_state_with_position(), market_request(OpenPosition(MARKET_REF, Decimal("20"), Decimal("0.56")))
    )

    state, commands = step(state, OrderRejected(T0_MS + 2_000, "r1", "fak_not_filled"))

    assert commands == [Notify(CHAT_ID, "❌ /open will-x-happen Yes rejected: fak_not_filled")]
    assert state.pending_orders == {}

    _, commands = step(state, book_at("0.50", at_ms=T0_MS + 3_000))

    assert commands == []


def test_open_and_close_refused_while_order_pending():
    state = fired_state()

    _, open_commands = step(
        state, market_request(OpenPosition(MARKET_REF, Decimal("20"), Decimal("0.56")), best_bid="0.45")
    )
    _, close_commands = step(state, market_request(ClosePosition(MARKET_REF), best_bid="0.45"))

    refusal = [Notify(CHAT_ID, "❌ an order is already in flight on will-x-happen Yes, retry in a moment")]
    assert open_commands == close_commands == refusal


def test_close_position_default_floor_is_bid_minus_slippage_rounded_up():
    state = running_state_with_position(config=make_config(close_slippage="0.025"))

    state, commands = step(state, market_request(ClosePosition(MARKET_REF), best_bid="0.52"))

    assert of_type(commands, SendSellOrder) == [SendSellOrder("r1", ASSET_ID, Decimal("100"), Decimal("0.50"))]
    assert state.pending_orders["r1"] == PendingOrder(
        "r1", ASSET_ID, "SELL", sent_at_ms=T0_MS + 1_000, trigger_id=None, reply_chat_id=CHAT_ID
    )


def test_close_position_default_floor_is_at_least_one_tick():
    _, commands = step(running_state_with_position(), market_request(ClosePosition(MARKET_REF), best_bid="0.01"))

    assert of_type(commands, SendSellOrder)[0].floor_price == Decimal("0.01")


def test_close_position_with_shares_and_floor_rounds_floor_up():
    request = ClosePosition(MARKET_REF, shares=Decimal("10"), floor_price=Decimal("0.485"))

    _, commands = step(running_state_with_position(), market_request(request))

    assert of_type(commands, SendSellOrder) == [SendSellOrder("r1", ASSET_ID, Decimal("10"), Decimal("0.49"))]


def test_close_position_refused_without_bid_or_position():
    _, no_bid_commands = step(running_state_with_position(), market_request(ClosePosition(MARKET_REF), best_bid=None))
    _, no_position_commands = step(running_state_with_position(size="0"), market_request(ClosePosition(MARKET_REF)))

    assert no_bid_commands == [Notify(CHAT_ID, "❌ no bid on will-x-happen Yes")]
    assert no_position_commands == [Notify(CHAT_ID, "❌ no position on will-x-happen Yes")]


def test_close_position_fill_replies_and_cancels_siblings_after_balance():
    state = running_state_with_position(triggers=(make_trigger(),))
    state, _ = step(state, market_request(ClosePosition(MARKET_REF)))

    state, commands = step(state, OrderFilled(T0_MS + 2_000, "r1", Decimal("100"), Decimal("50"), "order-1"))

    assert commands == [
        Notify(CHAT_ID, "✅ /close will-x-happen Yes: sold 100 @ 0.5 for 50 USDC"),
        FetchBalance(ASSET_ID),
    ]
    assert state.triggers[1].status == TriggerStatus.ARMED

    state, _ = step(state, BalanceLoaded(T0_MS + 3_000, ASSET_ID, Decimal(0)))

    assert state.triggers[1].status == TriggerStatus.CANCELLED
    assert ASSET_ID not in state.positions


# VALUATION


def test_value_position_marks_at_best_bid():
    valuation = value_position(Position(ASSET_ID, Decimal("100"), Decimal("0.50")), make_book("0.60"))

    assert (valuation.mark, valuation.value, valuation.cost) == (Decimal("0.60"), Decimal("60"), Decimal("50"))
    assert (valuation.pnl, valuation.pnl_pct) == (Decimal("10"), Decimal("20"))


def test_value_position_without_bid_marks_at_zero():
    no_bid = value_position(Position(ASSET_ID, Decimal("100"), Decimal("0.50")), make_book(None))
    no_book = value_position(Position(ASSET_ID, Decimal("100"), Decimal("0.50")), None)
    free = value_position(Position(ASSET_ID, Decimal("100"), Decimal("0")), make_book("0.60"))

    assert no_bid == no_book
    assert (no_bid.mark, no_bid.value, no_bid.pnl, no_bid.pnl_pct) == (0, 0, Decimal("-50"), Decimal("-100"))
    assert free.pnl_pct == 0
