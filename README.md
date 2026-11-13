# Polymarket TP/SL Bot

Stop-loss and take-profit trigger orders for Polymarket, controlled from Telegram.

*THIS SOFTWARE IS PROVIDED AS IS, WITHOUT WARRANTY OF ANY KIND. IT IS FOR EDUCATIONAL PURPOSES ONLY. DO NOT RISK MONEY
YOU ARE AFRAID TO LOSE. USE IT AT YOUR OWN RISK. THE AUTHORS ASSUME NO RESPONSIBILITY FOR YOUR TRADING RESULTS.*

Always start with `--dry-run` and read the code before trading real money.

## Layout

- `src/core.py`: pure, deterministic state machine, `step(state, event) -> (state, commands)`. No I/O and no clock.
- `src/format.py`, `src/parse.py`: Telegram text out and in (pure).
- Adapters behind `src/ports.py`: live `src/polymarket.py`, `src/telegram.py`, `src/geoblock.py`; dry-run
  `src/paper.py` plus a stdout notifier. `src/runner.py` wires them to the core.
- `replays/`: sample JSONL replays for `--dry-run`, also used as end-to-end test fixtures.
- `scripts/authorize_session_key.py`: one-off session key setup.
- `tests/`: core rules, parsing, rendering, paper exchange, end-to-end replays.

## How it works

Polymarket has no native stop orders. The bot watches the order book of every asset with a trigger. When the best
bid crosses the trigger price (at or below it for a stop-loss, at or above it for a take-profit), the trigger latches
and the bot sends a FAK (fill-and-kill) sell with a price floor.

It behaves like a stop-limit order. If the bid is below the floor, the bot waits and tells you once. A partial fill
retries on the next book with a bid at or above the floor. Nothing ever rests on the book.

Trigger orders live in memory only. A restart forgets them, and every start lists the positions that are unprotected.

## Safety model

- **Geoblock check on every live start.** Blocked: halt and exit with code `2`. Check failed: exit with code `1`.
- **Session key, not the owner key.** The key in `.env` can trade on the CLOB but cannot withdraw. The owner key is
  used once, from the process environment, and never stored.
- **One sell in flight per asset.** A second trigger on the same asset waits for the first order to resolve.
- **No resend on unknown outcome.** A timed-out order is reconciled against your account trades before anything else
  is sent. There are no blind retries.
- **Startup message.** Every start broadcasts which positions have no trigger protecting them.

## Setup

1. Install [uv](https://github.com/astral-sh/uv), then `uv sync`.
2. Create a Relayer API key for the Deposit Wallet owner at
   [polymarket.com/settings](https://polymarket.com/settings?tab=api-keys).
3. Run the one-off script with the owner key in the process environment (never in `.env`):

   ```bash
   OWNER_PRIVATE_KEY=... POLYMARKET_WALLET=... POLYMARKET_RELAYER_API_KEY=... POLYMARKET_RELAYER_API_KEY_ADDRESS=... \
     uv run scripts/authorize_session_key.py
   ```

   `POLYMARKET_WALLET` is the wallet address from the polymarket.com profile menu. The script authorizes a new
   CLOB-only session key and prints the `POLYMARKET_PRIVATE_KEY` and `POLYMARKET_WALLET` lines for `.env`.
   Trading approvals, including the Protocol V2 ones a session key cannot set itself, come from accepting the
   approval prompts in the Polymarket app.
4. `cp .env.example .env` and fill it in.

## Configuration

Live mode only; `--dry-run` reads no environment variables.

| Variable | Default | Meaning |
|---|---|---|
| `POLYMARKET_PRIVATE_KEY` | required | Session key from `scripts/authorize_session_key.py`. Trades on the CLOB, cannot withdraw. |
| `POLYMARKET_WALLET` | required | Deposit Wallet address the session key trades for. |
| `TG_BOT_TOKEN` | required | Telegram bot token. |
| `TG_CHAT_IDS` | required | Comma-separated chat id whitelist. Alerts go to all of them. |
| `STALE_SECONDS` | `30` | Book data older than this marks an asset stale. |
| `HEARTBEAT_HOURS` | `0` | Periodic health message interval. `0` turns it off. |
| `CLOSE_SLIPPAGE` | `0.02` | Default `/close` floor is the best bid minus this, rounded to the tick grid. |

## Running

```bash
caffeinate -is uv run main.py                         # live
uv run main.py --record recordings/session.jsonl      # live, and record books for replay
uv run main.py --dry-run replays/sl_gap.jsonl         # replay with paper fills, offline
```

`caffeinate` ships with macOS and keeps the Mac awake while the bot runs. Closing a MacBook lid still puts it to
sleep, and triggers are not watched while it sleeps.

## Telegram commands

`<market>` is an asset id, a Polymarket URL, or `<slug> <outcome>`. Optional numbers are positional: to set a size
on `/tp`, pass the floor too.

| Command | Example |
|---|---|
| `/sl <market> <trigger> <floor> [shares] [sell\|alert]` | `/sl will-trump-win-2028 yes 0.40 0.35` |
| | `/sl 7134…921 0.40 0.35 50 alert` |
| `/tp <market> <trigger> [floor] [shares]` | `/tp will-trump-win-2028 yes 0.80` |
| | `/tp https://polymarket.com/event/… 0.80 0.78 25` |
| `/open <market> <usdc> <max_price>` | `/open will-trump-win-2028 yes 20 0.55` |
| `/close <market> [shares] [floor]` | `/close will-trump-win-2028 yes` |
| | `/close will-trump-win-2028 yes 10 0.48` |
| `/price <market>` | `/price will-trump-win-2028 yes` |
| `/positions` | `/positions` |
| `/orders` | `/orders` |
| `/edit <id> <trigger> [floor]` | `/edit 3 0.42` |
| | `/edit 3 0.42 0.38` |
| `/cancel <id>` | `/cancel 3` |
| `/health` | `/health` |

- A stop-loss gap policy of `sell` (the default) still sells when the market gaps through the trigger. `alert` only
  notifies you and cancels the trigger.
- Omitting `shares` protects the whole position, measured when the trigger fires.
- The take-profit floor defaults to the trigger price.
- `/edit` works only while a trigger is armed. Ids accept `3` or `#3`.

Sample alerts from `replays/sl_gap.jsonl` (a stop-loss sells through a data gap, an `alert` stop-loss only warns):

```
🎯 SL #1 armed — fed-cuts-rates-in-december Yes: trigger 0.45, floor 0.40, size all, gap policy sell
🟠 Stale data: fed-cuts-rates-in-december Yes, btc-above-100k-on-dec-31 No
🟢 Data fresh again
⚠️ SL #2 btc-above-100k-on-dec-31 No gapped: bid 0.38 vs trigger 0.50 — not selling. /close or re-arm
⚡ SL #1 fed-cuts-rates-in-december Yes triggered: bid 0.42 vs trigger 0.45
📤 SL #1 fed-cuts-rates-in-december Yes: selling 100 shares, floor 0.40
✅ SL #1 fed-cuts-rates-in-december Yes: sold 100 @ 0.416 for 41.60 USDC
```

Sample `/orders` reply:

```
📦 Orders
#3 SL will-trump-win-2028 Yes · ARMED sell
  trigger 0.40 · floor 0.35 · size all · remaining 120
  bid 0.47 · distance +14.89%
#4 TP will-trump-win-2028 Yes · ARMED sell
  trigger 0.80 · floor 0.78 · size 25 · remaining 25
  bid 0.47 · distance -70.21%
```

## Dry run and replay format

`--dry-run` replays a JSONL file as fast as possible, fills orders against the replayed book, and prints
notifications to stdout. It needs no `.env` and no network. Prices and sizes are strings.

```jsonl
{"type":"market","asset_id":"123","slug":"will-x-happen","outcome":"Yes","tick_size":"0.01","min_order_size":"5"}
{"type":"position","asset_id":"123","size":"100","avg_price":"0.55"}
{"type":"collateral","usdc":"1000"}
{"type":"book","ts":1700000000000,"asset_id":"123","bids":[["0.52","200"],["0.50","500"]],"asks":[["0.54","300"]],"last_trade_price":"0.53"}
{"type":"command","ts":1700000001000,"text":"/sl will-x-happen yes 0.50 0.45"}
```

- `market` and `position` seed the paper exchange. `collateral` is optional (default 1000 USDC).
- `book` replaces the depth for that asset. Paper fills deplete it until the next `book` line.
- `command` is a Telegram request, parsed and resolved as in live mode.
- Every timed line (`ts` in milliseconds) also advances the bot clock.

`--record FILE` writes the same format from a live session.

## Tests

```bash
uv sync --extra dev && uv run pytest
```
