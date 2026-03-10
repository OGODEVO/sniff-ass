import asyncio
from dotenv import load_dotenv

load_dotenv()

from main import scan_active_markets
from edge_calculator import build_signal, should_trade
from price_feed import PriceFeed
from indicators import Indicators
from candle_tracker import CandleTracker
from clob_stream import ClobStream


async def _wait_for_orderbooks(clob: ClobStream, markets: list, timeout: float = 15.0) -> None:
    """Wait until each market has both asks, or until timeout expires."""
    deadline = asyncio.get_running_loop().time() + timeout

    while asyncio.get_running_loop().time() < deadline:
        ready = 0
        for market in markets:
            ask_up = clob.get_best_ask(market.token_id_up)
            ask_down = clob.get_best_ask(market.token_id_down)
            if ask_up is not None and ask_down is not None:
                ready += 1

        if ready == len(markets):
            return

        await asyncio.sleep(0.5)


async def run_diagnostic():
    print("Fetching active markets from Gamma...")
    markets = await scan_active_markets()
    if not markets:
        print("No active markets right now.")
        return

    ind = Indicators()
    ct = CandleTracker()
    pf = PriceFeed(ct, ind)
    clob = ClobStream()
    
    pf_task = asyncio.create_task(pf.start())
    clob_task = asyncio.create_task(clob.start())

    for m in markets:
        await clob.subscribe([m.token_id_up, m.token_id_down])

    print("Gathering real-time Binance, Chainlink, and CLOB data (waiting 10s)...")
    await asyncio.sleep(10)
    await _wait_for_orderbooks(clob, markets)
    
    print("\n" + "="*50)
    print("📈 DIAGNOSTIC SNAPSHOT: LIVE EDGE & PRICING")
    print("="*50)
    
    for m in markets:
        asset = m.asset
        candle = ct.get_candle_data(asset, m.timeframe_min)
        
        if not candle:
            print(f"[{asset}] ⚠️ No candle data. (Binance hasn't ticked yet or feed is catching up)")
            continue
            
        ask_up = clob.get_best_ask(m.token_id_up)
        ask_down = clob.get_best_ask(m.token_id_down)
        
        if not ask_up or not ask_down:
            print(f"[{asset}] ⚠️ Missing CLOB Orderbook asks.")
            continue
            
        rsi = ind.get_rsi(asset)
        vol = ind.get_volume_ratio(asset)
        
        sig = build_signal(
            asset=asset,
            market_id=m.condition_id,
            token_id_up=m.token_id_up,
            token_id_down=m.token_id_down,
            candle_open_price=candle["open_price"],
            current_price=candle["current_price"],
            elapsed_min=candle["elapsed_minutes"],
            remaining_min=candle["remaining_minutes"],
            market_price_up=ask_up,
            market_price_down=ask_down,
            rsi=rsi,
            volume_ratio=vol,
            momentum=0.0,
            timeframe_min=m.timeframe_min,
        )

        print(f"[{asset} {m.timeframe_min}m] WINDOW: {sig.entry_window} | ELAPSED: {sig.time_elapsed_min:.1f}m remaining: {sig.time_remaining_min:.1f}m")
        print(f"      Prices : Open = ${sig.candle_open_price:.2f} | Current = ${sig.current_price:.2f} ({sig.price_delta_pct*100:.3f}%)")
        print(f"      UP SIDE: MktAsk = {ask_up:.2f} | TrueProb = {sig.true_prob_up:.2f} | EDGE = {sig.edge_up:+.3f}")
        print(f"      DN SIDE: MktAsk = {ask_down:.2f} | TrueProb = {sig.true_prob_down:.2f} | EDGE = {sig.edge_down:+.3f}")
        
        # Test what the gating rules think
        trade, side, edge, price, prob = should_trade(sig)
        
        if trade:
            print(f"      🔥 DECISION: SIGNAL FIRED -> BUY {side} (Edge: {edge:.2f})")
        else:
            print(f"      🛑 DECISION: Rules Reject Trade (Usually due to <0.09 edge, >0.45 max edge, <0.15 min price, or stale RSI)")
        print("-" * 50)

    pf_task.cancel()
    clob_task.cancel()
    await asyncio.gather(pf_task, clob_task, return_exceptions=True)

if __name__ == "__main__":
    import logging
    # Silence the chatty bot loops to just see our print statements
    logging.getLogger("polybot").setLevel(logging.CRITICAL)
    asyncio.run(run_diagnostic())
