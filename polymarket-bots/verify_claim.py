"""Profit-claim checker: the wallet's own public record against the headline. Chapter 11.

    python verify_claim.py <wallet> <claimed profit>
    python verify_claim.py 0x5924ca480d8b08cd5f3e5811fa378c4082475af6 178000

Got a profile name instead of a wallet? This returns it as proxyWallet:
https://gamma-api.polymarket.com/public-search?q=<name>&search_profiles=true
Reads Polymarket's public Data API. No key.
"""
import argparse
import time
from datetime import datetime, timezone
import requests

DATA = "https://data-api.polymarket.com/v2"


def data_api(path, **params):
    r = requests.get(f"{DATA}/{path}", params=params, timeout=30)
    r.raise_for_status()
    return r.json()["data"]


def verify_claim(wallet, claimed_profit):
    stats = data_api("user-stats", user=wallet)
    pnl = stats["all_time_pnl"]
    points = data_api("user-pnl", user=wallet, interval="all",
                      fidelity="1d")["points"]
    last = data_api("activity", user=wallet, limit=1)

    print(f"Trades on record:    {stats['trades']:,}")
    print(f"Claimed profit:      ${claimed_profit:,.0f}")
    print(f"Realized profit:     ${pnl['realized_pnl']:,.0f}")
    deposits = pnl.get("deposits")
    print("Deposits on record:  "
          + ("not reported" if deposits is None else f"${deposits:,.0f}"))

    # realized_pnl is a running total: diff it month by month
    month_end = {}
    for p in points:
        key = datetime.fromtimestamp(p["timestamp"], timezone.utc).strftime("%Y-%m")
        month_end[key] = p["realized_pnl"]
    previous = 0.0
    for i, key in enumerate(sorted(month_end), start=1):
        print(f"  Month {i:>2}: ${month_end[key] - previous:+,.0f}")
        previous = month_end[key]

    if last:
        days = (time.time() - last[0]["timestamp"]) / 86400
        print(f"Last activity:       {days:.0f} days ago")
    gap = claimed_profit - pnl["realized_pnl"]
    print(f"Claim minus record:  ${gap:+,.0f}")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Check a profit claim against the wallet's public record.")
    parser.add_argument("wallet", help="the wallet the post names (0x + 40 hex)")
    parser.add_argument("claimed_profit", type=float, help="the profit the post claims, in $")
    args = parser.parse_args(argv)
    verify_claim(args.wallet, args.claimed_profit)


if __name__ == "__main__":
    main()
