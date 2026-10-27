"""Live best bid/ask and trades for one outcome token over the CLOB WebSocket. Chapter 2.

    python stream.py <token_id>   # any token id (the scanner's clobTokenIds)
    python stream.py              # no id: the busiest two-sided market's YES token

The book's run line was a comment, asyncio.run(stream_market("your_token_id")).
Ctrl+C stops it.
"""
import argparse
import asyncio
import json
import websockets

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


async def keep_alive(ws):
    while True:                      # the server expects a PING every 10 s
        await asyncio.sleep(10)
        await ws.send("PING")


async def stream_market(token_id):
    """Print live best bid/ask and trades for one outcome token."""
    async with websockets.connect(WS_URL) as ws:
        await ws.send(json.dumps({
            "assets_ids": [token_id],    # a list, plural key
            "type": "market"
        }))
        print(f"Subscribed to {token_id[:12]}...")
        pinger = asyncio.create_task(keep_alive(ws))
        try:
            while True:
                raw = await ws.recv()
                if raw == "PONG":
                    continue
                msg = json.loads(raw)
                # the first frame is a list of book snapshots
                for event in (msg if isinstance(msg, list) else [msg]):
                    kind = event.get("event_type")
                    if kind == "price_change":
                        for ch in event.get("price_changes", []):
                            if ch.get("asset_id") == token_id:
                                print(f"  Book: {ch['best_bid']} bid"
                                      f" / {ch['best_ask']} ask")
                    elif kind == "last_trade_price":
                        print(f"  Trade: {event['side']} "
                              f"{event['size']} @ {event['price']}")
        finally:
            pinger.cancel()


def busiest_yes_token():
    """YES token of the busiest market with a two-sided book, so the bare
    command has something to watch."""
    from scanner import get_book, get_markets, token_ids
    for market in get_markets(limit=20):
        ids = token_ids(market)
        if not ids:
            continue
        bids, asks = get_book(ids[0])
        if bids and asks:
            print(f"Watching: {market['question']}")
            return ids[0]
    raise SystemExit("None of the 20 busiest markets has a two-sided book right now.")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Stream one token's book and trades.")
    parser.add_argument("token_id", nargs="?",
                        help="outcome token id (default: the busiest two-sided market's YES token)")
    args = parser.parse_args(argv)
    token = args.token_id or busiest_yes_token()
    try:
        asyncio.run(stream_market(token))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
