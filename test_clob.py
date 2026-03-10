import asyncio
import logging
from clob_stream import ClobStream

logging.basicConfig(level=logging.DEBUG)

async def test():
    stream = ClobStream()
    asyncio.create_task(stream.start())
    await asyncio.sleep(2)
    # BTC 15m market ids change over time, just use an example token
    # Let's get the active markets first from Gamma and subscribe to them
    from main import scan_active_markets
    
    # We don't have main setup easily without env vars, but we can just use a hardcoded active token from the user's logs
    # UP SOL: 0x474ccae91f
    # DOWN BTC: 0x9be4adb9d0
    # DOWN XRP: 0x84abcf5218
    
    tokens = ["0x474ccae91f", "0x9be4adb9d0", "0x84abcf5218"]
    await stream.subscribe(tokens)
    
    for _ in range(10):
        await asyncio.sleep(1)
        for t in tokens:
            ask = stream.get_best_ask(t)
            bid = stream.get_best_bid(t)
            print(f"Token {t[:10]}: BID {bid} | ASK {ask}")

if __name__ == "__main__":
    asyncio.run(test())
