"""Shared helpers every later bot imports, plus a one-shot market snapshot. Chapter 2.

Reads Polymarket's free public APIs (Gamma for markets, CLOB for order books).
No key, no account, nothing is traded.

    python scanner.py            # the 5 busiest open markets
    python scanner.py --top 10
"""
import argparse
import json
import requests

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"


def get_markets(limit=100, **extra):
    """Open markets, busiest first. Gamma returns a plain list
    and caps limit at 100."""
    params = {"closed": "false", "limit": limit,
              "order": "volume24hr", "ascending": "false"}
    params.update(extra)
    r = requests.get(f"{GAMMA}/markets", params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def token_ids(market):
    """[yes_token, no_token] for a binary market."""
    return json.loads(market.get("clobTokenIds") or "[]")


def get_book(token_id):
    """Order book for one outcome token, best price first.

    The raw /book response lists bids from the lowest price
    up and asks from the highest price down, so index 0 of
    the raw lists is the WORST price on each side. Sort first.
    """
    r = requests.get(f"{CLOB}/book",
                     params={"token_id": token_id}, timeout=30)
    r.raise_for_status()
    book = r.json()
    bids = sorted(((float(b["price"]), float(b["size"]))
                   for b in book.get("bids", [])), reverse=True)
    asks = sorted((float(a["price"]), float(a["size"]))
                  for a in book.get("asks", []))
    return bids, asks   # bids[0] = best bid, asks[0] = best ask


def get_fee_rate(market):
    """Taker fee rate for this market (0.07 on crypto)."""
    sched = market.get("feeSchedule") or {}
    if not market.get("feesEnabled"):
        return 0.0
    return float(sched.get("rate", 0))


def calculate_fee(shares, price, fee_rate):
    """Polymarket taker fee. fee_rate is decimal (0.07)."""
    return shares * fee_rate * price * (1 - price)


# Printed in Chapter 2 next to calculate_fee() but outside the scanner.py
# listing; Appendix A files it under Chapter 2, so it lives here.
def min_spread_for_breakeven(price, fee_rate, execution="taker"):
    """Minimum spread needed to break even after fees."""
    if execution == "maker":
        return 0  # Makers pay no fee
    # Fee on buy side + fee on sell side
    return 2 * fee_rate * price * (1 - price)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Snapshot of the busiest open Polymarket markets.")
    parser.add_argument("--top", type=int, default=5,
                        help="how many markets to print (default 5)")
    args = parser.parse_args(argv)

    markets = get_markets()
    print("\n=== Polymarket Live Data ===")
    print(f"Open markets fetched: {len(markets)}\n")

    for i, market in enumerate(markets[:args.top]):
        ids = token_ids(market)
        if not ids:
            continue
        bids, asks = get_book(ids[0])
        print(f"Market {i+1}: {market['question']}")
        print(f"  24h volume: ${float(market.get('volume24hr') or 0):,.0f}"
              f" | taker fee rate: {get_fee_rate(market)}")
        if not bids or not asks:
            print("  One side of the book is empty\n")
            continue
        print(f"  YES price: ${bids[0][0]:.3f} bid / ${asks[0][0]:.3f} ask")
        print(f"  Spread: ${asks[0][0] - bids[0][0]:.4f}")
        print("  Bid depth (top 3):")
        for price, size in bids[:3]:
            print(f"    ${price:.3f} - {size:,.2f} shares")
        print("  Ask depth (top 3):")
        for price, size in asks[:3]:
            print(f"    ${price:.3f} - {size:,.2f} shares")
        print()


if __name__ == "__main__":
    main()
