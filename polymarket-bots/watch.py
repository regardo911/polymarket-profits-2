"""The 30-second loop: tightest spreads among the busiest markets. Chapter 2.

    python watch.py                       # 50 busiest, top 10, every 30 s
    python watch.py --markets 100 --interval 60

Ctrl+C stops it.
"""
import argparse
import time
import requests
from scanner import get_markets, get_book, token_ids


def watch(markets_to_read=50, show=10, interval=30):
    while True:
        spreads = []
        for market in get_markets(limit=markets_to_read):   # 50 busiest
            ids = token_ids(market)
            if not ids:
                continue
            try:
                bids, asks = get_book(ids[0])
            except requests.RequestException:
                continue
            if bids and asks:
                spreads.append({
                    "question": market["question"][:60],
                    "bid": bids[0][0],
                    "ask": asks[0][0],
                    "spread": asks[0][0] - bids[0][0],
                })

        spreads.sort(key=lambda s: s["spread"])   # tightest first

        print(f"\n--- Top {show} Tightest Spreads ({len(spreads)} books read) ---")
        for s in spreads[:show]:
            print(f"  ${s['spread']:.4f}  |  "
                  f"${s['bid']:.3f}/${s['ask']:.3f}  |  "
                  f"{s['question']}")

        print(f"\nRefreshing in {interval} seconds...")
        time.sleep(interval)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Tightest spreads, refreshed on a timer.")
    parser.add_argument("--markets", type=int, default=50,
                        help="busiest markets to read each pass (Gamma caps it at 100; default 50)")
    parser.add_argument("--top", type=int, default=10, help="rows to print (default 10)")
    parser.add_argument("--interval", type=int, default=30, help="seconds between passes (default 30)")
    args = parser.parse_args(argv)
    watch(args.markets, args.top, args.interval)


if __name__ == "__main__":
    main()
