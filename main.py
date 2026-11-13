import argparse
import asyncio
import logging
import os
import sys
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import httpx
from dotenv import load_dotenv
from polymarket import AsyncSecureClient, RequestRejectedError

from src.core import CoreConfig
from src.format import EMOJI_HALTED
from src.geoblock import HttpGeoblock, SkippedGeoblock
from src.polymarket import PolymarketAdapter
from src.runner import Adapters, run_live, run_replay
from src.telegram import StdoutNotifier, TelegramNotifier

logger = logging.getLogger(__name__)

DRY_RUN_CONFIG = CoreConfig(stale_ms=30_000, heartbeat_ms=0, close_slippage=Decimal("0.02"))
REQUIRED_ENV_VARS = ("POLYMARKET_PRIVATE_KEY", "POLYMARKET_WALLET", "TG_BOT_TOKEN", "TG_CHAT_IDS")


@dataclass(frozen=True)
class Config:
    private_key: str
    wallet: str
    bot_token: str
    chat_ids: tuple[int, ...]
    core: CoreConfig


def load_config() -> Config:
    missing = [name for name in REQUIRED_ENV_VARS if not os.getenv(name)]
    if missing:
        raise ValueError(f"missing env vars: {', '.join(missing)} (see .env.example)")
    return Config(
        private_key=os.environ["POLYMARKET_PRIVATE_KEY"],
        wallet=os.environ["POLYMARKET_WALLET"],
        bot_token=os.environ["TG_BOT_TOKEN"],
        chat_ids=tuple(int(chat_id) for chat_id in os.environ["TG_CHAT_IDS"].split(",")),
        core=CoreConfig(
            stale_ms=int(os.getenv("STALE_SECONDS", "30")) * 1000,
            heartbeat_ms=int(Decimal(os.getenv("HEARTBEAT_HOURS", "0")) * 3_600_000),
            close_slippage=Decimal(os.getenv("CLOSE_SLIPPAGE", "0.02")),
        ),
    )


def now_ms() -> int:
    return time.time_ns() // 1_000_000


async def live(config: Config, record_path: str | None) -> int:
    async with httpx.AsyncClient() as http_client:
        notifier = TelegramNotifier(config.bot_token, config.chat_ids, http_client)
        try:
            client = await AsyncSecureClient.create(private_key=config.private_key, wallet=config.wallet)
        except Exception as error:
            logger.exception("Polymarket client setup failed")
            await notifier.send(None, f"{EMOJI_HALTED} Halted: Polymarket client setup failed: {error}")
            return 2 if isinstance(error, RequestRejectedError) else 1
        async with client:
            if record_path is None:
                return await run_live(adapters(client, http_client, notifier, None), config.core, now_ms)
            Path(record_path).parent.mkdir(parents=True, exist_ok=True)
            with open(record_path, "w") as record_file:
                return await run_live(adapters(client, http_client, notifier, record_file), config.core, now_ms)


def adapters(client, http_client, notifier, record_file) -> Adapters:
    polymarket = PolymarketAdapter(client, now_ms, record_file)
    return Adapters(polymarket, polymarket, polymarket, HttpGeoblock(http_client), notifier)


def dry_run(replay_path: str) -> int:
    from src.paper import PaperExchange, read_replay

    paper = PaperExchange()
    replay_adapters = Adapters(paper, paper, paper, SkippedGeoblock(), StdoutNotifier())
    return asyncio.run(run_replay(replay_adapters, DRY_RUN_CONFIG, read_replay(replay_path)))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Stop-loss and take-profit trigger orders for Polymarket, controlled from Telegram.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Live (reads .env; caffeinate keeps the Mac awake while the lid is open):
    caffeinate -is uv run main.py

  Live, recording every order book update for later replay:
    uv run main.py --record recordings/session.jsonl

  Dry run: replay a JSONL file with paper fills, notifications on stdout, no .env, no network:
    uv run main.py --dry-run replays/sl_gap.jsonl
""",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", metavar="REPLAY", help="Replay a JSONL file offline with paper fills")
    mode.add_argument("--record", metavar="FILE", help="Run live and write order books to FILE as a replay")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.dry_run is not None:
        logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s %(message)s")
        sys.exit(dry_run(args.dry_run))

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # httpx logs every request URL at INFO, and Telegram URLs contain the bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        config = load_config()
    except ValueError as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
    sys.exit(asyncio.run(live(config, args.record)))


if __name__ == "__main__":
    main()
