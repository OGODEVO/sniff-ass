import asyncio
import websockets

async def test_ws(url):
    print(f"Testing {url}...")
    try:
        async with websockets.connect(url, ping_interval=None) as ws:
            print(f"SUCCESS: Connected to {url}")
            msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
            print(f"Received data: {msg[:100]}")
            return True
    except Exception as e:
        print(f"FAILED: {e}")
        return False

async def main():
    urls = [
        "wss://stream.binance.com:9443/stream?streams=btcusdt@trade",
        "wss://stream.binance.us:9443/stream?streams=btcusdt@trade",
        "wss://data-stream.binance.vision:9443/stream?streams=btcusdt@trade",
        "wss://dex.binance.org/api/ws"
    ]
    for u in urls:
        await test_ws(u)

asyncio.run(main())
