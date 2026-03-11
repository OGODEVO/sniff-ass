"""
CLOB WebSocket stream — real-time orderbook prices from Polymarket.

Endpoint: wss://ws-subscriptions-clob.polymarket.com/ws/market
No authentication required for market data.
Subscribes to token IDs and maintains a local L2 order book cache.
"""

from __future__ import annotations

import asyncio
import json

import websockets

from logger import log

CLOB_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


class ClobStream:
    """
    Maintains a real-time cache of the L2 order book for subscribed tokens
    via the Polymarket CLOB WebSocket.
    """

    def __init__(self) -> None:
        # token_id → {"bids": {price: size}, "asks": {price: size}}
        self._books: dict[str, dict[str, dict[float, float]]] = {}
        self._subscribed_tokens: set[str] = set()
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._connected = False

    # ── Public getters ────────────────────────────────────────

    def get_best_ask(self, token_id: str) -> float | None:
        """Return cached best ask price for a token, or None if unknown."""
        book = self._books.get(token_id)
        if not book or not book["asks"]:
            return None
        return min(book["asks"].keys())

    def get_best_bid(self, token_id: str) -> float | None:
        """Return cached best bid price for a token, or None if unknown."""
        book = self._books.get(token_id)
        if not book or not book["bids"]:
            return None
        return max(book["bids"].keys())

    def has_price(self, token_id: str) -> bool:
        """Returns True if the order book has both bids and asks."""
        book = self._books.get(token_id)
        return bool(book and book["bids"] and book["asks"])

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

    def _ensure_book(self, asset_id: str) -> None:
        if asset_id not in self._books:
            self._books[asset_id] = {"bids": {}, "asks": {}}

    def _process_message(self, msg: dict) -> None:
        """Parse incoming WS message and update full L2 book cache."""
        event_type = msg.get("event_type", "")

        if event_type == "book":
            # Full orderbook snapshot
            asset_id = msg.get("asset_id", "")
            if not asset_id:
                return
            self._ensure_book(asset_id)
            
            # Clear old book on a full snapshot
            self._books[asset_id]["bids"].clear()
            self._books[asset_id]["asks"].clear()
            
            bids = msg.get("bids", [])
            asks = msg.get("asks", [])
            
            for b_level in bids:
                p = float(b_level.get("price", 0))
                s = float(b_level.get("size", 0))
                if s > 0:
                    self._books[asset_id]["bids"][p] = s
                    
            for a_level in asks:
                p = float(a_level.get("price", 0))
                s = float(a_level.get("size", 0))
                if s > 0:
                    self._books[asset_id]["asks"][p] = s
                    
            best_bid = self.get_best_bid(asset_id) or 0.0
            best_ask = self.get_best_ask(asset_id) or 0.0
            log.debug("[CLOB_WS] Book %s: bid=%.4f ask=%.4f", asset_id[:12], best_bid, best_ask)

        elif event_type == "price_change":
            # Incremental L2 update
            changes = msg.get("changes", [])
            for change in changes:
                asset_id = change.get("asset_id", "")
                if not asset_id:
                    continue
                self._ensure_book(asset_id)
                
                price = float(change.get("price", 0))
                side = change.get("side", "").upper()
                size = float(change.get("size", 0))
                
                book_side = self._books[asset_id]["bids"] if side == "BUY" else self._books[asset_id]["asks"]
                
                if size == 0:
                    book_side.pop(price, None)
                else:
                    book_side[price] = size

        elif event_type == "last_trade_price":
            # Just informational, last trade price doesn't affect the limit order book resting liquidity
            pass

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

            except asyncio.CancelledError:
                self._connected = False
                self._ws = None
                log.info("[CLOB_WS] Cancelled")
                raise
            except (websockets.ConnectionClosed, Exception) as exc:
                self._connected = False
                self._ws = None
                log.warning("[CLOB_WS] Disconnected: %s — reconnecting in 3s", exc)
                # Clear orderbook on disconnect because it will be stale
                self._books.clear()
                await asyncio.sleep(3)
