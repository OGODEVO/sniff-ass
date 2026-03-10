import asyncio
import os
from dotenv import load_dotenv

import executor
from clob_stream import ClobStream
from executor import Executor
import main
from logger import log

load_dotenv()

async def force_trade():
    """Force a $5 FAK order on the current active BTC UP 15m market."""
    log.info("Starting Forced Trade Test...")
    
    # 1. Get an active BTC market
    markets = await main.scan_active_markets()
    btc_market = next((m for m in markets if m.asset == "BTC"), None)
    
    if not btc_market:
        log.error("No active BTC 15m market found to test on.")
        return
        
    log.info(f"Targeting Market: {btc_market.condition_id} ({btc_market.asset})")
    
    # 2. Get current ask price
    stream = ClobStream()
    asyncio.create_task(stream.start())
    
    # Wait for ws to connect and book to populate
    log.info("Waiting 5 seconds for orderbook cache to populate...")
    await asyncio.sleep(5)
    await stream.subscribe([btc_market.token_id_up])
    await asyncio.sleep(2)
    
    best_ask = stream.get_best_ask(btc_market.token_id_up)
    
    if not best_ask:
        # Fallback to gamma
        best_ask = btc_market.outcome_price_up
        log.warning(f"WS Ask missing, falling back to Gamma price: {best_ask}")
    else:
        log.info(f"Current WS Best Ask: {best_ask}")
        
    # We want this to fill, so we will pay an aggressive premium to cross spread
    execution_price = round(best_ask + 0.05, 2)
    # Cap at 99c
    execution_price = min(0.99, execution_price)
    
    log.info(f"Attempting to force buying UP BTC at aggressive price {execution_price} for $5.00")
    
    # 3. Execute
    exec_client = Executor()
    
    # Turn off dry run for this specific test
    executor.DRY_RUN = False
    
    results = await exec_client.execute_split_order(
        token_id=btc_market.token_id_up,
        price=execution_price,
        total_size=5.00
    )
    
    if results:
        log.info(f"✅ FORCED TRADE SUCCESS: {results}")
    else:
        log.error("❌ FORCED TRADE FAILED. Check error logs.")
        
    # Hard exit
    os._exit(0)

if __name__ == "__main__":
    asyncio.run(force_trade())
