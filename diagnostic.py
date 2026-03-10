import asyncio
from dotenv import load_dotenv

load_dotenv()

from main import scan_active_markets
from edge_calculator import build_signal, should_trade
from price_feed import PriceFeed
from indicators import Indicators
from candle_tracker import CandleTracker
from clob_stream import ClobStream

async def run_diagnostic():
    print("Fetching active markets from Gamma...")
    markets = await scan_active_markets()
    if not markets:
        print("No active markets right now.")
        return

    ind = Indicators()
    ct = CandleTracker()
    pf = PriceFeed(ind, ct)
    clob = ClobStream()
    
    pf_task = asyncio.create_task(pf.start())
    clob_task = asyncio.create_task(clob.start())
    
    print("Gathering real-time Binance, Chainlink, and CLOB data (waiting 10s)...")
    await asyncio.sleep(10)
    
    for m in markets:
        await clob.subscribe([m.token_id_up, m.token_id_down])
        
    await asyncio.sleep(3)  # Wait for WebSocket books to populate
    
    print("\n" + "="*50)
    print("📈 DIAGNOSTIC SNAPSHOT: LIVE EDGE & PRICING")
    print("="*50)
    
    for m in markets:
        asset = m.asset
        candle = ct.get_candle_data(asset)
        
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
            momentum=0.0
        )
        
        print(f"[{asset}] WINDOW: {sig.entry_window} | ELAPSED: {sig.time_elapsed_min:.1f}m remaining: {sig.time_remaining_min:.1f}m")
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

if __name__ == "__main__":
    import logging
    # Silence the chatty bot loops to just see our print statements
    logging.getLogger("polybot").setLevel(logging.CRITICAL)
    asyncio.run(run_diagnostic())
