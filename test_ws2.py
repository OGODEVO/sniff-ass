import asyncio
import json
import logging
import websockets
import requests

logging.basicConfig(level=logging.INFO)

async def test():
    uri = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    try:
        async with websockets.connect(uri) as ws:
            # Get an active market manually
            # Let's get "Will Bitcoin hit $100k today?" or similar high-volume market
            req = requests.get("https://gamma-api.polymarket.com/events?active=true&limit=50")
            events = req.json()
            asset_ids = []
            for e in events:
                for m in e.get("markets", []):
                    if m.get("closed", False) is False:
                        tokens = m.get("clobTokenIds", [])
                        if len(tokens) > 0:
                            asset_ids.append(tokens[0])
            
            if not asset_ids:
                print("No active markets found")
                return
                
            sub = {"assets_ids": asset_ids[:2], "type": "market"}
            print(f"Subscribing to {sub}")
            await ws.send(json.dumps(sub))
            
            for _ in range(15):
                msg = await ws.recv()
                print(msg[:500])
    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    asyncio.run(test())
