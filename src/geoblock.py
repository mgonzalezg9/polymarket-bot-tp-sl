import httpx

from constants import READ_TIMEOUT_SECONDS
from src.core import GeoblockChecked

GEOBLOCK_URL = "https://polymarket.com/api/geoblock"


class HttpGeoblock:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def check(self, at_ms: int) -> GeoblockChecked:
        try:
            response = await self._client.get(GEOBLOCK_URL, timeout=READ_TIMEOUT_SECONDS)
            response.raise_for_status()
            body = response.json()
            if not isinstance(body.get("blocked"), bool):
                raise ValueError(f"unexpected geoblock response: {body}")
            return GeoblockChecked(at_ms, blocked=body["blocked"], country=str(body.get("country", "")))
        except Exception as error:
            return GeoblockChecked(at_ms, blocked=False, country="", error=str(error) or type(error).__name__)


class SkippedGeoblock:
    async def check(self, at_ms: int) -> GeoblockChecked:
        return GeoblockChecked(at_ms, blocked=False, country="dry-run")
