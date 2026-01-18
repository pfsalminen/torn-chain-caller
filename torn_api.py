import httpx
from dataclasses import dataclass
from typing import Optional


TORN_API_BASE = "https://api.torn.com"


@dataclass
class TornUser:
    user_id: int
    name: str
    faction_id: Optional[int]
    faction_name: Optional[str]


@dataclass
class ChainInfo:
    current: int
    max: int
    timeout: int  # seconds until chain breaks
    cooldown: int  # seconds if in cooldown


class TornAPIError(Exception):
    pass


async def validate_api_key(api_key: str) -> TornUser:
    """
    Validate a torn.com API key and return user info.
    Raises TornAPIError if the key is invalid or the API call fails.
    """
    url = f"{TORN_API_BASE}/user/?selections=profile&key={api_key}"

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, timeout=10.0)
            response.raise_for_status()
        except httpx.HTTPError as e:
            raise TornAPIError(f"HTTP error: {e}")

        data = response.json()

        if "error" in data:
            error_msg = data["error"].get("error", "Unknown error")
            raise TornAPIError(f"Torn API error: {error_msg}")

        faction = data.get("faction", {})
        faction_id = faction.get("faction_id")
        faction_name = faction.get("faction_name")

        # faction_id of 0 means no faction
        if faction_id == 0:
            faction_id = None
            faction_name = None

        return TornUser(
            user_id=data["player_id"],
            name=data["name"],
            faction_id=faction_id,
            faction_name=faction_name,
        )


async def get_chain_info(api_key: str) -> ChainInfo:
    """
    Fetch current chain information for the user's faction.
    Raises TornAPIError if the API call fails.
    """
    url = f"{TORN_API_BASE}/faction/?selections=chain&key={api_key}"

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, timeout=10.0)
            response.raise_for_status()
        except httpx.HTTPError as e:
            raise TornAPIError(f"HTTP error: {e}")

        data = response.json()

        if "error" in data:
            error_msg = data["error"].get("error", "Unknown error")
            raise TornAPIError(f"Torn API error: {error_msg}")

        chain = data.get("chain", {})

        return ChainInfo(
            current=chain.get("current", 0),
            max=chain.get("max", 0),
            timeout=chain.get("timeout", 0),
            cooldown=chain.get("cooldown", 0),
        )
