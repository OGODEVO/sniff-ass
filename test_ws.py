import asyncio
import json
import logging
import websockets

async def test():
    uri = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    try:
        async with websockets.connect(uri) as ws:
            # We need an active market.
            import requests
            res = requests.get("https://gamma-api.polymarket.com/events?active=true&limit=10")
            events = res.json()
            asset_ids = []
            for e in events:
                for m in e.get("markets", []):
                    if m.get("closed") is False:
                        asset_ids.extend([m.get("clobTokenIds", [])[0]])
            if not asset_ids:
                print("No active markets")
                return
            
            sub = {"assets_ids": asset_ids[:5], "type": "market"}
            print(f"Subscribing to {sub}")
            await ws.send(json.dumps(sub))
            
            for _ in range(10):
                msg = await ws.recv()
                print(msg[:500])
    except Exception as e:
        print("Error:", e)

asyncio.run(test())
