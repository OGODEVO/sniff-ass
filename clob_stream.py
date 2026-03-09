"""
CLOB WebSocket stream — real-time orderbook prices from Polymarket.

Endpoint: wss://ws-subscriptions-clob.polymarket.com/ws/market
No authentication required for market data.
Subscribes to token IDs and receives live price_change + book events.
"""

from __future__ import annotations

import asyncio
import json

import websockets

from logger import log

CLOB_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


class ClobStream:
    """
    Maintains a real-time cache of best bid/ask prices for subscribed tokens
    via the Polymarket CLOB WebSocket.
    """

    def __init__(self) -> None:
        # token_id → {"best_bid": float, "best_ask": float}
        self._prices: dict[str, dict[str, float]] = {}
        self._subscribed_tokens: set[str] = set()
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._connected = False

    # ── Public getters ────────────────────────────────────────

    def get_best_ask(self, token_id: str) -> float | None:
        """Return cached best ask price for a token, or None if unknown."""
        data = self._prices.get(token_id)
        if data and data.get("best_ask", 0) > 0:
            return data["best_ask"]
        return None

    def get_best_bid(self, token_id: str) -> float | None:
        """Return cached best bid price for a token, or None if unknown."""
        data = self._prices.get(token_id)
        if data and data.get("best_bid", 0) > 0:
            return data["best_bid"]
        return None

    def has_price(self, token_id: str) -> bool:
        return token_id in self._prices

    # ── Subscription management ───────────────────────────────

    async def subscribe(self, token_ids: list[str]) -> None:
        """
        Subscribe to price updates for the given token IDs.
        Can be called multiple times — only new tokens are subscribed.
        """
        new_tokens = [t for t in token_ids if t and t not in self._subscribed_tokens]
        if not new_tokens:
            return

        if not self._ws or not self._connected:
            log.debug("[CLOB_WS] Not connected yet, queueing %d tokens", len(new_tokens))
            self._subscribed_tokens.update(new_tokens)
            return

        sub_msg = {
            "type": "market",
            "assets_ids": new_tokens,
        }
        try:
            await self._ws.send(json.dumps(sub_msg))
            self._subscribed_tokens.update(new_tokens)
            log.info("[CLOB_WS] Subscribed to %d new tokens (total: %d)",
                     len(new_tokens), len(self._subscribed_tokens))
        except Exception as exc:
            log.warning("[CLOB_WS] Subscribe error: %s", exc)

    # ── Message processing ────────────────────────────────────

    def _process_message(self, msg: dict) -> None:
        """Parse incoming WS message and update price cache."""
        event_type = msg.get("event_type", "")

        if event_type == "book":
            # Full orderbook snapshot
            asset_id = msg.get("asset_id", "")
            if not asset_id:
                return
            bids = msg.get("bids", [])
            asks = msg.get("asks", [])
            best_bid = float(bids[0].get("price", 0)) if bids else 0
            best_ask = float(asks[0].get("price", 0)) if asks else 0
            self._prices[asset_id] = {"best_bid": best_bid, "best_ask": best_ask}
            log.debug("[CLOB_WS] Book %s: bid=%.4f ask=%.4f", asset_id[:12], best_bid, best_ask)

        elif event_type == "price_change":
            # Incremental price update
            changes = msg.get("changes", [])
            for change in changes:
                asset_id = change.get("asset_id", "")
                if not asset_id:
                    continue
                if asset_id not in self._prices:
                    self._prices[asset_id] = {"best_bid": 0, "best_ask": 0}
                price = float(change.get("price", 0))
                side = change.get("side", "").upper()
                if side == "BUY" and price > 0:
                    self._prices[asset_id]["best_bid"] = price
                elif side == "SELL" and price > 0:
                    self._prices[asset_id]["best_ask"] = price

        elif event_type == "last_trade_price":
            asset_id = msg.get("asset_id", "")
            price = float(msg.get("price", 0))
            if asset_id and price > 0:
                if asset_id not in self._prices:
                    self._prices[asset_id] = {"best_bid": 0, "best_ask": 0}
                # Use last trade as fallback for both
                if self._prices[asset_id]["best_ask"] == 0:
                    self._prices[asset_id]["best_ask"] = price
                if self._prices[asset_id]["best_bid"] == 0:
                    self._prices[asset_id]["best_bid"] = price

    # ── Main stream loop ──────────────────────────────────────

    async def start(self) -> None:
        """Connect to CLOB WS and stream price updates forever."""
        log.info("[CLOB_WS] Connecting to %s", CLOB_WS_URL)

        while True:
            try:
                async with websockets.connect(
                    CLOB_WS_URL,
                    open_timeout=30,
                    ping_interval=20,
                    ping_timeout=20,
                ) as ws:
                    self._ws = ws
                    self._connected = True
                    log.info("[CLOB_WS] Connected")

                    # Re-subscribe to any queued tokens
                    if self._subscribed_tokens:
                        sub_msg = {
                            "type": "market",
                            "assets_ids": list(self._subscribed_tokens),
                        }
                        await ws.send(json.dumps(sub_msg))
                        log.info("[CLOB_WS] Re-subscribed to %d tokens", len(self._subscribed_tokens))

                    async for raw in ws:
                        try:
                            parsed = json.loads(raw)
                            # WS can send a single dict or a list of dicts
                            msgs = parsed if isinstance(parsed, list) else [parsed]
                            for msg in msgs:
                                if isinstance(msg, dict):
                                    self._process_message(msg)
                        except json.JSONDecodeError:
                            continue

            except (websockets.ConnectionClosed, Exception) as exc:
                self._connected = False
                self._ws = None
                log.warning("[CLOB_WS] Disconnected: %s — reconnecting in 3s", exc)
                await asyncio.sleep(3)
