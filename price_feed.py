"""
Dual price feed — Chainlink on-chain (resolution truth) + Binance kline WS (RSI/volume).

Multi-asset: BTC, ETH, SOL, XRP.
Chainlink aggregators polled periodically per asset.
Binance kline_1m streams feed into CandleTracker + Indicators.
"""

import asyncio
import json
import time
from datetime import datetime, timezone

import websockets
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

from candle_tracker import CandleTracker
from config import (
    BINANCE_SYMBOLS,
    BINANCE_WS_BASE,
    CHAINLINK_AGGREGATORS,
    POLYGON_RPC_URL,
    SUPPORTED_ASSETS,
)
from indicators import Indicators
from logger import log

# ── Chainlink Aggregator V3 minimal ABI ──────────────────────
AGGREGATOR_ABI = [
    {
        "inputs": [],
        "name": "latestRoundData",
        "outputs": [
            {"name": "roundId", "type": "uint80"},
            {"name": "answer", "type": "int256"},
            {"name": "startedAt", "type": "uint256"},
            {"name": "updatedAt", "type": "uint256"},
            {"name": "answeredInRound", "type": "uint80"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    },
]


class PriceFeed:
    """Manages multi-asset price feeds from Chainlink + Binance."""

    def __init__(
        self,
        candle_tracker: CandleTracker,
        indicators: Indicators,
    ) -> None:
        self._candle_tracker = candle_tracker
        self._indicators = indicators

        # Per-asset latest Chainlink prices
        self._chainlink_prices: dict[str, float] = {}
        self._chainlink_ts: dict[str, float] = {}

        # Web3 setup — deferred to first poll to avoid blocking on import
        self._w3: Web3 | None = None
        self._aggregators: dict[str, any] = {}
        self._decimals: dict[str, int] = {}
        self._chainlink_initialized = False
        self._historical_price_cache: dict[tuple[str, int], float] = {}
        self._block_ts_cache: dict[int, int] = {}

    def _init_chainlink(self) -> None:
        """Lazy-init Web3 + Chainlink contracts (called on first poll)."""
        if self._chainlink_initialized:
            return
        self._chainlink_initialized = True

        log.info("[PRICE_FEED] Initialising Chainlink aggregators…")
        self._w3 = Web3(Web3.HTTPProvider(POLYGON_RPC_URL, request_kwargs={"timeout": 10}))
        self._w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

        for asset, addr in CHAINLINK_AGGREGATORS.items():
            try:
                contract = self._w3.eth.contract(
                    address=Web3.to_checksum_address(addr),
                    abi=AGGREGATOR_ABI,
                )
                self._aggregators[asset] = contract
                self._decimals[asset] = contract.functions.decimals().call()
                log.info("[PRICE_FEED] Loaded Chainlink %s aggregator", asset)
            except Exception as exc:
                log.warning("[PRICE_FEED] Failed to load %s aggregator: %s", asset, exc)
                self._decimals[asset] = 8

    # ── Public getters ────────────────────────────────────────

    def get_price(self, asset: str) -> float:
        """Best available price — prefer Chainlink, fall back to candle tracker."""
        now = time.time()
        ts = self._chainlink_ts.get(asset, 0)
        cl_price = self._chainlink_prices.get(asset, 0.0)
        ct_price = self._candle_tracker.get_current_price(asset)

        if cl_price > 0 and (now - ts) < 120:
            return cl_price
        if ct_price > 0:
            return ct_price
        return cl_price

    def get_chainlink_price(self, asset: str) -> float:
        """Strictly get the last known Chainlink price (no Binance fallback)."""
        return self._chainlink_prices.get(asset, 0.0)

    def _get_block_timestamp(self, block_number: int) -> int:
        cached = self._block_ts_cache.get(block_number)
        if cached is not None:
            return cached
        if self._w3 is None:
            return 0
        block = self._w3.eth.get_block(block_number)
        ts = int(block["timestamp"])
        self._block_ts_cache[block_number] = ts
        return ts

    def _find_block_at_or_before(self, target_ts: int) -> int | None:
        if self._w3 is None:
            return None

        latest_block = self._w3.eth.block_number
        latest_ts = self._get_block_timestamp(latest_block)
        if target_ts >= latest_ts:
            return latest_block

        lo = 1
        hi = latest_block
        best: int | None = None
        while lo <= hi:
            mid = (lo + hi) // 2
            mid_ts = self._get_block_timestamp(mid)
            if mid_ts <= target_ts:
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1
        return best

    def get_chainlink_price_at(self, asset: str, target_time: datetime) -> float:
        """
        Return the Chainlink price whose round was already published at `target_time`.
        This aligns candle opens/resolution to Polymarket's oracle-timestamp rulebook.
        """
        self._init_chainlink()
        contract = self._aggregators.get(asset)
        decimals = self._decimals.get(asset, 8)
        if contract is None or self._w3 is None:
            return 0.0

        if target_time.tzinfo is None:
            target_time = target_time.replace(tzinfo=timezone.utc)
        target_ts = int(target_time.timestamp())
        cache_key = (asset, target_ts)
        cached = self._historical_price_cache.get(cache_key)
        if cached is not None:
            return cached

        start_block = self._find_block_at_or_before(target_ts)
        if start_block is None:
            return 0.0

        block_number = start_block
        for _ in range(256):
            try:
                round_data = contract.functions.latestRoundData().call(block_identifier=block_number)
            except Exception:
                break

            raw_price = int(round_data[1])
            updated_at = int(round_data[3])
            if raw_price > 0 and updated_at > 0 and updated_at <= target_ts:
                price = raw_price / (10 ** decimals)
                self._historical_price_cache[cache_key] = price
                return price

            if block_number <= 1:
                break
            block_number = max(1, block_number - 64)

        return 0.0

    # ── Chainlink polling (all assets) ────────────────────────

    async def poll_chainlink(self, interval: float = None) -> None:
        """Poll all Chainlink aggregators every `interval` seconds."""
        from config import CHAINLINK_POLL_SEC
        if interval is None:
            interval = CHAINLINK_POLL_SEC
        self._init_chainlink()  # lazy init on first poll
        log.info("[PRICE_FEED] Starting Chainlink polling for %s (every %.0fs)",
                 list(self._aggregators.keys()), interval)
        while True:
            for asset, contract in self._aggregators.items():
                try:
                    data = contract.functions.latestRoundData().call()
                    raw_price = data[1]
                    price = raw_price / (10 ** self._decimals[asset])
                    self._chainlink_prices[asset] = price
                    self._chainlink_ts[asset] = time.time()
                    self._candle_tracker.update_price(asset, price)
                    log.debug("[PRICE_FEED] Chainlink %s = $%.2f", asset, price)
                except Exception as exc:
                    log.warning("[PRICE_FEED] Chainlink %s error: %s", asset, exc)
            await asyncio.sleep(interval)

    # ── Binance kline WebSocket (all assets, combined stream) ─

    async def stream_binance_klines(self) -> None:
        """
        Connect to Binance combined kline_1m stream for all assets.
        Feeds close prices + volume into Indicators, and price into CandleTracker.
        """
        streams = "/".join(
            f"{sym}@kline_1m" for sym in BINANCE_SYMBOLS.values()
        )
        url = f"{BINANCE_WS_BASE}{streams}"
        log.info("[PRICE_FEED] Connecting to Binance kline WS: %s", url)

        # Reverse map: "btcusdt" → "BTC"
        sym_to_asset = {v: k for k, v in BINANCE_SYMBOLS.items()}

        while True:
            try:
                async with websockets.connect(url) as ws:
                    log.info("[PRICE_FEED] Binance kline WS connected")
                    async for raw in ws:
                        msg = json.loads(raw)
                        data = msg.get("data", msg)  # combined stream wraps in "data"
                        kline = data.get("k", {})
                        if not kline:
                            continue

                        symbol = kline.get("s", "").lower()
                        asset = sym_to_asset.get(symbol)
                        if not asset:
                            continue

                        close = float(kline.get("c", 0))
                        volume = float(kline.get("v", 0))
                        is_closed = kline.get("x", False)  # kline closed?

                        # Always update candle tracker with latest price
                        if close > 0:
                            self._candle_tracker.update_price(asset, close)

                        # Only feed completed klines into indicators
                        if is_closed and close > 0:
                            self._indicators.update_kline(asset, close, volume)
                            log.debug(
                                "[PRICE_FEED] Kline closed %s: $%.2f vol=%.0f",
                                asset, close, volume,
                            )

            except asyncio.CancelledError:
                log.info("[PRICE_FEED] Binance kline WS cancelled")
                raise
            except (websockets.ConnectionClosed, Exception) as exc:
                log.warning("[PRICE_FEED] Binance WS error: %s — reconnecting in 5s", exc)
                await asyncio.sleep(5)

    # ── Launch all feeds ──────────────────────────────────────

    async def start(self) -> None:
        """Start all price feeds as concurrent tasks."""
        await asyncio.gather(
            self.poll_chainlink(),
            self.stream_binance_klines(),
        )
