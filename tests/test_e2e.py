import asyncio

from main import DRY_RUN_CONFIG
from src.core import GeoblockChecked
from src.geoblock import SkippedGeoblock
from src.paper import PaperExchange, read_replay
from src.runner import Adapters, run_live, run_replay
from tests.helpers import T0_MS


class RecordingNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[int | None, str]] = []

    async def send(self, chat_id: int | None, text: str) -> None:
        self.sent.append((chat_id, text))

    async def requests(self):
        return
        yield


class FakeGeoblock:
    def __init__(self, blocked: bool = False, error: Exception | None = None) -> None:
        self.blocked = blocked
        self.error = error

    async def check(self, at_ms: int) -> GeoblockChecked:
        if self.error is not None:
            raise self.error
        return GeoblockChecked(at_ms, blocked=self.blocked, country="US")


def make_adapters(geoblock, notifier: RecordingNotifier) -> Adapters:
    paper = PaperExchange()
    return Adapters(paper, paper, paper, geoblock, notifier)


def replay(path: str) -> tuple[int, RecordingNotifier]:
    notifier = RecordingNotifier()
    exit_code = asyncio.run(run_replay(make_adapters(SkippedGeoblock(), notifier), DRY_RUN_CONFIG, read_replay(path)))
    return exit_code, notifier


def emojis(notifier: RecordingNotifier) -> list[str]:
    return [text.split()[0] for _, text in notifier.sent]


def test_replay_sl_gap_sells_through_gap_and_alerts_on_alert_policy(capsys):
    exit_code, notifier = replay("replays/sl_gap.jsonl")

    assert exit_code == 0
    assert emojis(notifier) == ["🚀", "🎯", "🎯", "🟠", "🟢", "⚠️", "⚡", "📤", "✅", "📦"]
    assert "sold 100 @ 0.416 for 41.60 USDC" in notifier.sent[8][1]
    summary = capsys.readouterr().out
    assert "#1 SL fed-cuts-rates-in-december Yes FILLED" in summary
    assert "#2 SL btc-above-100k-on-dec-31 No CANCELLED" in summary


def test_replay_tp_partial_fills_in_two_parts_and_resizes_sibling(capsys):
    exit_code, notifier = replay("replays/tp_partial.jsonl")

    assert exit_code == 0
    assert emojis(notifier) == ["🚀", "🎯", "🎯", "📦", "⚡", "📤", "🌓", "⏳", "💼", "📤", "✅", "🔧", "📦"]
    assert "resized to 80 shares" in notifier.sent[11][1]
    summary = capsys.readouterr().out
    assert "#1 TP gpt-6-released-in-2026 Yes FILLED: sold 120 shares for 84.10 USDC" in summary
    assert "#2 SL gpt-6-released-in-2026 Yes ARMED" in summary


def test_live_geoblocked_exits_2_with_broadcast():
    notifier = RecordingNotifier()

    exit_code = asyncio.run(run_live(make_adapters(FakeGeoblock(blocked=True), notifier), DRY_RUN_CONFIG, lambda: T0_MS))

    assert exit_code == 2
    assert notifier.sent == [(None, "⚠️ Geoblocked (US) — exiting")]


def test_live_geoblock_failure_exits_1():
    notifier = RecordingNotifier()
    geoblock = FakeGeoblock(error=ConnectionError("unreachable"))

    exit_code = asyncio.run(run_live(make_adapters(geoblock, notifier), DRY_RUN_CONFIG, lambda: T0_MS))

    assert exit_code == 1
    assert notifier.sent == [(None, "⚠️ Geoblock check failed: unreachable — exiting")]
