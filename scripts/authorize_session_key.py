"""Authorize a CLOB-only session key for the Deposit Wallet and print the .env lines for the bot.

OWNER_PRIVATE_KEY=... POLYMARKET_WALLET=... POLYMARKET_RELAYER_API_KEY=... POLYMARKET_RELAYER_API_KEY_ADDRESS=... \
  uv run scripts/authorize_session_key.py
"""

import asyncio
import os

from eth_account import Account
from polymarket import AsyncSecureClient, RelayerApiKey, SessionKeyKnownScope


async def main() -> None:
    session_key = Account.create()
    async with await AsyncSecureClient.create(
        private_key=os.environ["OWNER_PRIVATE_KEY"],
        # The profile-menu address; the SDK default is derived from the key and may not be the funded wallet.
        wallet=os.environ["POLYMARKET_WALLET"],
        api_key=RelayerApiKey(
            key=os.environ["POLYMARKET_RELAYER_API_KEY"],
            address=os.environ["POLYMARKET_RELAYER_API_KEY_ADDRESS"],
        ),
    ) as owner_client:
        # ponytail: trading approvals (incl. Protocol V2) come from the Polymarket app's prompts; the SDK's
        # setup_trading_approvals() would also grant an unused unlimited Perps allowance.
        await owner_client.authorize_session_key(address=session_key.address, scopes=[SessionKeyKnownScope.CLOB])
        print(f"POLYMARKET_PRIVATE_KEY=0x{session_key.key.hex().removeprefix('0x')}")
        print(f"POLYMARKET_WALLET={owner_client.wallet}")


asyncio.run(main())
