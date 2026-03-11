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

        # Market has ended — resolve using Polymarket's exact oracle timestamps
        asset = trade["asset"]
        close_price = price_feed.get_chainlink_price_at(asset, end_time)
        open_price = trade["candle_open"]

        if close_price <= 0 or open_price <= 0:
            still_pending.append(trade)  # retry next cycle
            continue

        went_up = close_price >= open_price
        won = (trade["side"] == "UP" and went_up) or \
              (trade["side"] == "DOWN" and not went_up)

        paper.resolve_trade(trade["paper_entry"], won)
        daily_pnl += trade["paper_entry"]["pnl"]
        risk_mgr.record_exit()

    _pending_trades = still_pending


# ── Main loop ─────────────────────────────────────────────────

# Cache of active markets updated by background task
_cached_markets = []

async def background_market_scanner() -> None:
    """Poll Gamma API every 30s to find active 5m/15m markets, decoupling from fast loop."""
    global _cached_markets
    while True:
        try:
            markets = await scan_active_markets()
            _cached_markets = markets
            token_ids: list[str] = []
            for market in markets:
                token_ids.extend([market.token_id_up, market.token_id_down])
            if token_ids:
                await clob_stream.subscribe(token_ids)
        except Exception as exc:
            log.error("[SCANNER] Error fetching markets: %s", exc)
        await asyncio.sleep(SCAN_INTERVAL_SEC)

async def bot_loop(
    price_feed: PriceFeed,
    indicators: Indicators,
    risk_mgr: RiskManager,
    paper: PaperTrader,
    clob_stream: ClobStream,
    executor: Executor,
) -> None:
    """Main sub-second fast loop for signal generation and execution."""
    global daily_pnl, _trade_count, _cached_markets

    log.info("[BOT] Starting high-speed evaluation loop (0.5s intervals)")
    last_heartbeat = 0.0

    while True:
        now_ts = _time.time()
        if now_ts - last_heartbeat >= 15:
            log.info("[BOT] Heartbeat | cached_markets=%d", len(_cached_markets))
            last_heartbeat = now_ts

        # 1. Resolve any expired paper trades (does not block, uses local data)
        await resolve_pending_trades()

        # 1.5 Reset same-direction correlation counter for this scan cycle
        reset_cycle_directions()

        markets = _cached_markets
        if not markets:
            await asyncio.sleep(1.0)
            continue

        for market in markets:
            try:
                asset = market.asset

                now = datetime.now(timezone.utc)
                open_price = price_feed.get_chainlink_price_at(asset, market.event_start_time)
                current_price = price_feed.get_chainlink_price(asset) or price_feed.get_price(asset)
                elapsed_min = (now - market.event_start_time).total_seconds() / 60.0
                remaining_min = (market.end_time - now).total_seconds() / 60.0

                if open_price <= 0 or current_price <= 0 or elapsed_min < 0 or remaining_min < 0:
                    continue

                # Need real-time indicators
                rsi = indicators.get_rsi(asset)
                vol_ratio = indicators.get_volume_ratio(asset)
                momentum = indicators.get_momentum(asset)

                log.debug(
                    "[BOT] Before subscribe | %s %sm %s",
                    asset, market.timeframe_min, market.condition_id[:12],
                )

                # Subscribe new tokens to CLOB WS (no-op if already subscribed)
                await clob_stream.subscribe([market.token_id_up, market.token_id_down])

                log.debug(
                    "[BOT] After subscribe | %s %sm %s",
                    asset, market.timeframe_min, market.condition_id[:12],
                )

                # ONLY use CLOB WS prices (real-time). Fast loop cannot block on REST calls.
                # If WS has no price, we skip. We do not fallback to Gamma here to avoid stale edges.
                ws_up = clob_stream.get_best_ask(market.token_id_up)
                ws_down = clob_stream.get_best_ask(market.token_id_down)

                if ws_up is None or ws_down is None:
                    log.warning(
                        "[BOT] No CLOB ask for %s %sm (up=%s down=%s) - skipping market",
                        asset,
                        market.timeframe_min,
                        market.token_id_up[:12] if ws_up is None else "ok",
                        market.token_id_down[:12] if ws_down is None else "ok",
                    )
                    continue

                mkt_up = ws_up
                mkt_down = ws_down

                if mkt_up <= 0.01 or mkt_down <= 0.01 or mkt_up >= 0.99 or mkt_down >= 0.99:
                    log.warning(
                        "[BOT] Bad prices for %s %sm (up=%.2f down=%.2f) - skipping",
                        asset, market.timeframe_min, mkt_up, mkt_down,
                    )
                    continue

                # Build full signal
                sig = build_signal(
                    asset=asset,
                    market_id=market.condition_id,
                    token_id_up=market.token_id_up,
                    token_id_down=market.token_id_down,
                    candle_open_price=open_price,
                    current_price=current_price,
                    elapsed_min=round(elapsed_min, 2),
                    remaining_min=round(remaining_min, 2),
                    market_price_up=mkt_up,
                    market_price_down=mkt_down,
                    rsi=rsi,
                    volume_ratio=vol_ratio,
                    momentum=momentum,
                    timeframe_min=market.timeframe_min,
                )

                # Gate check
                trade, side, edge, mkt_price, true_prob = should_trade(sig)
                if not trade:
                    continue

                token_id = market.token_id_up if side == "UP" else market.token_id_down
                ws_exec_price = ws_up if side == "UP" else ws_down

                # Risk check
                current_bankroll = paper.bankroll
                if not risk_mgr.check_all(token_id, edge, true_prob, current_bankroll, daily_pnl):
                    continue

                # Size via Kelly — use LIVE bankroll
                size = calc_kelly_size(
                    edge=edge,
                    market_price=mkt_price,
                    true_prob=true_prob,
                    entry_window=sig.entry_window,
                    bankroll=current_bankroll,
                )

                if size < 1.0:
                    continue

                log.info(
                    "[BOT] 🎯 %s %s %sm %s | window=%s | edge=%.2f price=%.2f $%.2f | RSI=%.0f vol=%.1f",
                    side, asset, market.timeframe_min, market.condition_id[:12], sig.entry_window,
                    edge, mkt_price, size, rsi, vol_ratio,
                )

                if DRY_RUN:
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
                        "candle_open": open_price,
                        "end_time": market.end_time,
                    })
                    _trade_count += 1
                else:
                    # Live orders must use a real CLOB quote, not a fallback price.
                    exec_price = ws_exec_price if ws_exec_price is not None else executor.get_best_price(token_id, "BUY")
                    if exec_price is None or exec_price <= 0:
                        log.warning(
                            "[BOT] No executable CLOB BUY price for %s %sm %s - skipping live order",
                            asset, market.timeframe_min, market.condition_id[:12],
                        )
                        continue
                    asyncio.create_task(
                        executor.execute_split_order(token_id, exec_price, size)
                    )
                    _trade_count += 1

                # Record entry in risk manager against the asset (not condition id, to avoid duplicates across loops)
                risk_mgr.record_entry(token_id)
            except Exception as exc:
                log.exception(
                    "[BOT] Market iteration crashed | %s %sm %s | %s",
                    market.asset, market.timeframe_min, market.condition_id[:12], exc,
                )

        # Sleep a tiny amount to prevent CPU spin, effectively polling local data at 2 Hz
        await asyncio.sleep(0.5)


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

    # Start background tasks
    feed_task = asyncio.create_task(price_feed.start())
    clob_task = asyncio.create_task(clob_stream.start())
    scanner_task = asyncio.create_task(background_market_scanner())

    # Wait for initial data
    log.info("[BOT] Waiting 8s for initial price data…")
    await asyncio.sleep(8)
    
    # Start the fast evaluation loop
    bot_task = asyncio.create_task(
        bot_loop(price_feed, indicators, risk_mgr, paper, clob_stream, executor)
    )

    _start_time = _time.time()
    last_hourly = _start_time

    try:
        while not _shutdown:
            # Hourly stats
            now = _time.time()
            if now - last_hourly >= 3600:
                elapsed_hrs = (now - _start_time) / 3600
                log.info(
                    "[BOT] ⏱ HOURLY | %.1fh | trades=%d pending=%d | P&L=$%.2f | bankroll=$%.2f",
                    elapsed_hrs, _trade_count, len(_pending_trades), daily_pnl, paper.bankroll,
                )
                last_hourly = now

            await asyncio.sleep(10)
    finally:
        # Resolve remaining trades
        await resolve_pending_trades()
        tasks = [feed_task, clob_task, scanner_task, bot_task]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
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
