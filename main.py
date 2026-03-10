"""
Polymarket 15-Minute Crypto Bot V2 — main orchestrator.

Multi-asset (BTC/ETH/SOL/XRP), RSI + volume confirmation,
order splitting, paper trading with auto-resolution,
CLOB prices via real-time WebSocket stream.
"""

import asyncio
import signal
import sys
import time as _time
from datetime import datetime, timezone

from candle_tracker import CandleTracker
from clob_stream import ClobStream
from config import (
    BANKROLL,
    DRY_RUN,
    MIN_EDGE,
    SCAN_INTERVAL_SEC,
    SUPPORTED_ASSETS,
)
from edge_calculator import build_signal, should_trade, reset_cycle_directions
from executor import Executor
from indicators import Indicators
from logger import log
from market_scanner import scan_active_markets
from paper_trader import PaperTrader
from position_sizer import calc_kelly_size
from price_feed import PriceFeed
from risk_manager import RiskManager

# ── Shared state ──────────────────────────────────────────────
candle_tracker = CandleTracker()
indicators = Indicators()
price_feed = PriceFeed(candle_tracker, indicators)
clob_stream = ClobStream()
executor = Executor()
risk_mgr = RiskManager(BANKROLL)
paper = PaperTrader(BANKROLL)

daily_pnl: float = 0.0
_shutdown = False
_pending_trades: list[dict] = []  # trades awaiting resolution
_start_time: float = 0.0
_trade_count: int = 0


def _handle_signal(sig, _frame):
    global _shutdown
    log.info("[BOT] Received signal %s — shutting down gracefully…", sig)
    _shutdown = True


# ── Trade resolution ──────────────────────────────────────────

async def resolve_pending_trades():
    """Check if any pending paper trades can be resolved (market ended)."""
    global daily_pnl, _pending_trades
    now = datetime.now(timezone.utc)
    still_pending = []

    for trade in _pending_trades:
        end_time = trade["end_time"]
        if now < end_time:
            still_pending.append(trade)
            continue

        # Market has ended — check final price vs candle open
        asset = trade["asset"]
        # MUST use Chainlink oracle for resolution to match Polymarket exact payouts
        current_price = price_feed.get_chainlink_price(asset)
        open_price = trade["candle_open"]

        if current_price <= 0 or open_price <= 0:
            still_pending.append(trade)  # retry next cycle
            continue

        went_up = current_price >= open_price
        won = (trade["side"] == "UP" and went_up) or \
              (trade["side"] == "DOWN" and not went_up)

        paper.resolve_trade(trade["paper_entry"], won)
        daily_pnl += trade["paper_entry"]["pnl"]
        risk_mgr.record_exit()

    _pending_trades = still_pending


# ── Main loop ─────────────────────────────────────────────────

async def bot_loop():
    """One cycle: resolve old trades, scan markets, signal, size, execute."""
    global daily_pnl, _trade_count

    # 1. Resolve any expired paper trades
    await resolve_pending_trades()

    # 1.5 Reset same-direction correlation counter for this scan cycle
    reset_cycle_directions()

    # 2. Scan for active markets (all assets)
    markets = await scan_active_markets()
    if not markets:
        log.info("[BOT] No active Up/Down markets found")
        return

    for market in markets:
        asset = market.asset

        # Ensure we have price data for this asset
        candle_data = candle_tracker.get_candle_data(asset)
        if not candle_data:
            log.debug("[BOT] No candle data for %s yet, skipping", asset)
            continue

        # Get indicator values
        rsi = indicators.get_rsi(asset)
        vol_ratio = indicators.get_volume_ratio(asset)
        momentum = indicators.get_momentum(asset)

        # Subscribe new tokens to CLOB WS (no-op if already subscribed)
        await clob_stream.subscribe([market.token_id_up, market.token_id_down])

        # Use CLOB WS prices (real-time), fall back to Gamma-reported
        mkt_up = market.outcome_price_up
        mkt_down = market.outcome_price_down
        ws_up = clob_stream.get_best_ask(market.token_id_up)
        ws_down = clob_stream.get_best_ask(market.token_id_down)
        if ws_up is not None:
            mkt_up = ws_up
        else:
            log.debug("[BOT] ⚠️ CLOB WS price missing for %s UP — using Gamma (may be stale)", asset)
        if ws_down is not None:
            mkt_down = ws_down
        else:
            log.debug("[BOT] ⚠️ CLOB WS price missing for %s DOWN — using Gamma (may be stale)", asset)

        # Build full signal
        sig = build_signal(
            asset=asset,
            market_id=market.condition_id,
            token_id_up=market.token_id_up,
            token_id_down=market.token_id_down,
            candle_open_price=candle_data["open_price"],
            current_price=candle_data["current_price"],
            elapsed_min=candle_data["elapsed_minutes"],
            remaining_min=candle_data["remaining_minutes"],
            market_price_up=mkt_up,
            market_price_down=mkt_down,
            rsi=rsi,
            volume_ratio=vol_ratio,
            momentum=momentum,
        )

        # Entry decision (RSI + volume + window + edge checks)
        trade, side, edge, mkt_price, true_prob = should_trade(sig)
        if not trade:
            continue

        token_id = market.token_id_up if side == "UP" else market.token_id_down

        # Risk check (daily loss, drawdown, position count, min prob, duplicate entry)
        if not risk_mgr.check_all(token_id, edge, true_prob, paper.bankroll, daily_pnl):
            continue

        # Size via Kelly — use LIVE bankroll, not static config
        size = calc_kelly_size(
            edge=edge,
            market_price=mkt_price,
            true_prob=true_prob,
            entry_window=sig.entry_window,
            bankroll=paper.bankroll,
        )
        if size < 1.0:
            log.debug("[BOT] Size $%.2f too small, skipping", size)
            continue

        log.info(
            "[BOT] 🎯 %s %s %s | window=%s | edge=%.2f price=%.2f $%.2f | RSI=%.0f vol=%.1f",
            side, asset, market.condition_id[:12], sig.entry_window,
            edge, mkt_price, size, rsi, vol_ratio,
        )

        # Paper trade entry + track for resolution
        paper_entry = paper.record_entry(
            asset=asset,
            market_id=market.condition_id,
            side=side,
            market_price=mkt_price,
            true_prob=true_prob,
            edge=edge,
            size_usd=size,
            entry_window=sig.entry_window,
        )
        _pending_trades.append({
            "paper_entry": paper_entry,
            "asset": asset,
            "side": side,
            "candle_open": candle_data["open_price"],
            "end_time": market.end_time,
        })
        _trade_count += 1

        # Execute (split into micro-orders)
        results = await executor.execute_split_order(
            token_id=token_id,
            price=mkt_price,
            total_size=size,
        )

        if results:
            risk_mgr.record_entry(token_id)


async def main():
    """Entry point: start feeds and run bot loop."""
    global _start_time, daily_pnl

    log.info("=" * 60)
    log.info("[BOT] Polymarket Crypto Bot V2")
    log.info("[BOT] Assets: %s", SUPPORTED_ASSETS)
    log.info("[BOT] DRY_RUN=%s  BANKROLL=$%.2f  MIN_EDGE=%.2f", DRY_RUN, BANKROLL, MIN_EDGE)
    log.info("=" * 60)

    if DRY_RUN:
        log.info("[BOT] 🧪 DRY RUN MODE — no real orders will be placed")

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # Start price feeds + CLOB WS in background
    feed_task = asyncio.create_task(price_feed.start())
    clob_task = asyncio.create_task(clob_stream.start())

    # Wait for initial data
    log.info("[BOT] Waiting 8s for initial price data…")
    await asyncio.sleep(8)

    _start_time = _time.time()
    last_hourly = _start_time

    try:
        while not _shutdown:
            try:
                await bot_loop()
            except Exception as exc:
                log.error("[BOT] Loop error: %s", exc, exc_info=True)

            # Hourly stats
            now = _time.time()
            if now - last_hourly >= 3600:
                elapsed_hrs = (now - _start_time) / 3600
                log.info(
                    "[BOT] ⏱ HOURLY | %.1fh | trades=%d pending=%d | P&L=$%.2f | bankroll=$%.2f",
                    elapsed_hrs, _trade_count, len(_pending_trades), daily_pnl, paper.bankroll,
                )
                last_hourly = now

            await asyncio.sleep(SCAN_INTERVAL_SEC)
    finally:
        # Resolve remaining trades
        await resolve_pending_trades()
        feed_task.cancel()
        clob_task.cancel()
        elapsed = (_time.time() - _start_time) / 3600
        log.info("=" * 60)
        log.info("[BOT] FINAL RESULTS (%.1fh)", elapsed)
        log.info("[BOT] Total trades: %d", _trade_count)
        log.info("[BOT] Session P&L: $%.2f", daily_pnl)
        paper.daily_report()
        log.info("[BOT] CSV log: logs/paper_trades.csv")
        log.info("=" * 60)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("[BOT] Interrupted. Exiting.")
        sys.exit(0)
