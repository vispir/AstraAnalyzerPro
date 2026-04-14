"""
OANDA market data — live price, M15/H1 candles.
Used only for data (charts). Trade execution goes through MT5.

Caches last fetch to avoid hammering the API on every 15-min cycle.
"""

import logging
import time
import requests
from datetime import datetime, timezone
from typing import Optional
import pandas as pd

from astra_v2 import config

logger = logging.getLogger(__name__)

OANDA_LIVE_URL = "https://api-fxtrade.oanda.com"
OANDA_PRACTICE_URL = "https://api-fxpractice.oanda.com"

_price_cache: dict = {}  # {"price": float, "ts": float}
PRICE_CACHE_TTL_SECONDS = 5


class OANDAClient:
    """Minimal OANDA REST v20 client for market data only."""

    def __init__(self):
        base = OANDA_LIVE_URL if config.OANDA_ENV == "live" else OANDA_PRACTICE_URL
        self.base = base
        self.headers = {
            "Authorization": f"Bearer {config.OANDA_API_KEY}",
            "Content-Type": "application/json",
        }
        self._session = requests.Session()
        self._session.headers.update(self.headers)

    def _get(self, path: str, params: dict = None) -> dict:
        url = f"{self.base}{path}"
        resp = self._session.get(url, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def get_current_price(self) -> float:
        """
        Get current mid price for XAU/USD.
        Cached for 5 seconds to avoid rate limits.
        """
        now = time.time()
        if _price_cache and (now - _price_cache.get("ts", 0)) < PRICE_CACHE_TTL_SECONDS:
            return _price_cache["price"]

        data = self._get(
            f"/v3/accounts/{config.OANDA_ACCOUNT_ID}/pricing",
            params={"instruments": config.SYMBOL},
        )
        prices = data.get("prices", [])
        if not prices:
            raise RuntimeError("No price data from OANDA")

        p = prices[0]
        bid = float(p["bids"][0]["price"])
        ask = float(p["asks"][0]["price"])
        mid = (bid + ask) / 2

        _price_cache["price"] = mid
        _price_cache["ts"] = now
        return mid

    def get_candles(
        self,
        granularity: str = "M15",
        count: int = 200,
        from_dt: datetime = None,
    ) -> pd.DataFrame:
        """
        Fetch OANDA candles for XAU/USD.

        Args:
            granularity: "M15", "H1", "H4", "D"
            count: number of bars (max 5000)
            from_dt: if set, fetch from this datetime instead of using count

        Returns DataFrame: open, high, low, close, volume (UTC index)
        """
        params = {
            "granularity": granularity,
            "price": "M",  # midpoint
        }
        if from_dt:
            params["from"] = from_dt.strftime("%Y-%m-%dT%H:%M:%S.000000000Z")
            params["count"] = count
        else:
            params["count"] = count

        data = self._get(
            f"/v3/instruments/{config.SYMBOL}/candles",
            params=params,
        )

        candles = data.get("candles", [])
        if not candles:
            return pd.DataFrame()

        rows = []
        for c in candles:
            if not c.get("complete", True):
                continue  # skip incomplete current bar
            mid = c["mid"]
            rows.append({
                "timestamp": pd.Timestamp(c["time"]),
                "open": float(mid["o"]),
                "high": float(mid["h"]),
                "low": float(mid["l"]),
                "close": float(mid["c"]),
                "volume": int(c.get("volume", 0)),
            })

        df = pd.DataFrame(rows).set_index("timestamp")
        df.index = pd.to_datetime(df.index, utc=True)
        return df

    def get_account_summary(self) -> dict:
        """Get account balance, unrealized PnL, and NAV."""
        data = self._get(f"/v3/accounts/{config.OANDA_ACCOUNT_ID}/summary")
        acc = data.get("account", {})
        return {
            "balance": float(acc.get("balance", 0)),
            "nav": float(acc.get("NAV", 0)),
            "unrealized_pnl": float(acc.get("unrealizedPL", 0)),
            "margin_used": float(acc.get("marginUsed", 0)),
        }


# Module-level singleton (lazy init)
_client: Optional[OANDAClient] = None


def get_client() -> OANDAClient:
    global _client
    if _client is None:
        _client = OANDAClient()
    return _client


def current_price() -> float:
    return get_client().get_current_price()


def candles(granularity: str = "M15", count: int = 200) -> pd.DataFrame:
    return get_client().get_candles(granularity=granularity, count=count)
