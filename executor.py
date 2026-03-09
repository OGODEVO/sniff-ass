"""
CLOB executor — wraps py-clob-client for order placement.
Supports order splitting with jitter and dry-run guard.
"""

from __future__ import annotations

import asyncio
import random

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY

from config import (
    CHAIN_ID,
    CLOB_HOST,
    DRY_RUN,
    ORDER_DELAY_MAX,
    ORDER_DELAY_MIN,
    ORDER_SPLIT_COUNT,
    PRIVATE_KEY,
    PROXY_ADDRESS,
)
from logger import log


def split_order(total_size: float, num_splits: int = ORDER_SPLIT_COUNT) -> list[float]:
    """
    Split a large order into micro-orders with slight random jitter.
    Enforces Polymarket's $5.00 minimum order size constraint.
    """
    if total_size < 5.0:
        return [round(total_size, 2)]

    # Reduce splits to ensure chunks average at least ~$5.50
    while total_size / num_splits < 5.5 and num_splits > 1:
        num_splits -= 1

    if num_splits <= 1:
        return [round(total_size, 2)]

    base_size = total_size / num_splits
    orders: list[float] = []
    remaining = total_size

    for _ in range(num_splits - 1):
        jitter = random.uniform(0.85, 1.15)
        size = round(base_size * jitter, 2)
        # Ensure size is at least $5, but don't leave remaining < $5
        size = max(5.0, min(size, remaining - 5.0))
        if size < 5.0:
            break
        orders.append(size)
        remaining -= size

    if remaining >= 5.0:
        orders.append(round(remaining, 2))
    elif orders:
        orders[-1] = round(orders[-1] + remaining, 2)
    else:
        orders.append(round(remaining, 2))

    return orders


class Executor:
    """Manages CLOB connection, order splitting, and execution."""

    def __init__(self) -> None:
        self._client: ClobClient | None = None

    def _ensure_client(self) -> ClobClient:
        """Lazy-init the CLOB client."""
        if self._client is not None:
            return self._client

        if not PRIVATE_KEY:
            raise RuntimeError("PRIVATE_KEY not set — cannot initialise CLOB client")

        self._client = ClobClient(
            CLOB_HOST,
            key=PRIVATE_KEY,
            chain_id=CHAIN_ID,
            signature_type=2,
            funder=PROXY_ADDRESS or None,
        )
        creds = self._client.create_or_derive_api_creds()
        self._client.set_api_creds(creds)
        log.info("[EXECUTOR] CLOB client initialised (chain=%d)", CHAIN_ID)
        return self._client

    # ── Price fetch ───────────────────────────────────────────

    def get_best_price(self, token_id: str, side: str = "BUY") -> float | None:
        """Fetch best price from CLOB orderbook."""
        try:
            client = self._ensure_client()
            resp = client.get_price(token_id, side)
            price = float(resp.get("price", 0))
            return price if price > 0 else None
        except Exception as exc:
            log.warning("[EXECUTOR] get_price error: %s", exc)
            return None

    # ── Single order placement ────────────────────────────────

    def _place_single(self, token_id: str, price: float, size_usd: float) -> dict | None:
        """Place a single FOK order."""
        if price <= 0:
            return None
        num_shares = round(size_usd / price, 2)

        if DRY_RUN:
            log.info(
                "[EXECUTOR] 🧪 DRY RUN — BUY %s | price=%.2f $%.2f (%.0f shares)",
                token_id[:16], price, size_usd, num_shares,
            )
            return {"dry_run": True, "token_id": token_id, "price": price, "size": size_usd}

        try:
            client = self._ensure_client()
            order = client.create_order(OrderArgs(
                price=price,
                size=num_shares,
                side=BUY,
                token_id=token_id,
            ))
            result = client.post_order(order, OrderType.FOK)
            log.info(
                "[EXECUTOR] ✅ Fill: %s | price=%.2f shares=%.0f | %s",
                token_id[:16], price, num_shares, result,
            )
            return result
        except Exception as exc:
            log.error("[EXECUTOR] ❌ Order failed: %s", exc)
            return None

    # ── Split order execution ─────────────────────────────────

    async def execute_split_order(
        self,
        token_id: str,
        price: float,
        total_size: float,
    ) -> list[dict]:
        """
        Split total_size into micro-orders and execute concurrently.
        Returns list of results.
        """
        chunks = split_order(total_size)
        log.info(
            "[EXECUTOR] Concurrently executing %d micro-orders for $%.2f (%s)",
            len(chunks), total_size, token_id[:16],
        )

        # Thread the blocking synchronous client calls to execute concurrently
        tasks = [
            asyncio.to_thread(self._place_single, token_id, price, chunk_size)
            for chunk_size in chunks
        ]
        
        results_raw = await asyncio.gather(*tasks)
        return [r for r in results_raw if r]
