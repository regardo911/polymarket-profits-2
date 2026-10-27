"""Fee-aware pair and nested-market arbitrage scanner, on paper. Chapter 4.

Prices every Up/Down window, strike ladder and deadline series it can find,
subtracts both taker fees, and paper-buys both legs only when the gap clears
min_gap with at least 10 pairs at the best asks. Paper pairs are held to
resolution and settled at the payout (settle_resolved_positions in trainer.py).

    python arbitrage.py                                         # Moderate: $0.005, $50, 30 s
    python arbitrage.py --min-gap 0.01 --max-cost 25 --scan-interval 60   # Conservative

Paper trading only. Educational software, not financial advice.
"""
import argparse
import re
import time
from datetime import datetime
import requests
from scanner import GAMMA, get_book, get_fee_rate, token_ids
from trainer import PaperPortfolio, settle_resolved_positions


def get_events(**params):
    """Open Gamma events (each carries its markets)."""
    params = {"closed": "false", "limit": 100,
              "order": "volume24hr", "ascending": "false", **params}
    r = requests.get(f"{GAMMA}/events", params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def find_pair_windows(assets=("btc", "eth")):
    """The live 5-minute Up/Down market for each asset."""
    now = int(time.time())
    start = now - now % 300          # windows start every 300 s
    windows = []
    for asset in assets:
        slug = f"{asset}-updown-5m-{start}"
        r = requests.get(f"{GAMMA}/events/slug/{slug}", timeout=30)
        if r.ok and r.json().get("markets"):
            windows.append(r.json()["markets"][0])
    return windows


def strike(market):
    """'80,000' -> 80000.0, or None if it isn't a plain number."""
    text = (market.get("groupItemTitle") or "").replace(",", "")
    return float(text) if re.fullmatch(r"\d+(\.\d+)?", text) else None


def find_nested_sets():
    """Events whose markets are nested, smallest event first.

    Ladder: "Bitcoin above ___": P(above $82K) <= P(above $80K).
    Deadline: "... by ...?": P(by an earlier date) <= P(by a later one).
    Each set is ordered so every market contains the next one.
    """
    events = (get_events(tag_slug="bitcoin")
              + get_events(tag_slug="ethereum")
              + get_events())
    sets, seen = [], set()
    for ev in events:
        if ev.get("id") in seen or ev.get("negRisk"):
            continue
        seen.add(ev.get("id"))
        live = [m for m in ev.get("markets", [])
                if m.get("acceptingOrders") and token_ids(m)]
        title = ev.get("title", "")
        if "above ___" in title:
            ladder = [m for m in live if strike(m) is not None]
            ladder.sort(key=strike)              # low strike first
            kind = "LADDER"
        elif re.search(r"\bby\b", title) and title.endswith("?"):
            ladder = sorted(live, key=lambda m: m["endDate"],
                            reverse=True)        # latest deadline first
            if len({m["endDate"] for m in ladder}) < len(ladder):
                continue                         # deadlines not distinct
            kind = "DEADLINE"
        else:
            continue
        if len(ladder) >= 2:
            sets.append({"kind": kind, "title": title,
                         "markets": ladder})
    return sets


def best_ask(token_id):
    """(price, size) at the best ask, or None."""
    bids, asks = get_book(token_id)
    return asks[0] if asks else None


def price_pair(leg_a, leg_b):
    """Cost of buying one share of each leg, after taker fees.

    Each leg is (market, token_id). Both legs together pay at
    least $1.00 at resolution, so anything under $1.00 after
    fees is locked-in profit.
    """
    quotes = []
    for market, token in (leg_a, leg_b):
        quote = best_ask(token)
        if quote is None:
            return None
        quotes.append((market, token, quote[0], quote[1]))

    cost = sum(q[2] for q in quotes)
    fees = sum(get_fee_rate(q[0]) * q[2] * (1 - q[2])
               for q in quotes)
    return {
        "legs": [{"market": q[0], "token_id": q[1], "ask": q[2]}
                 for q in quotes],
        "cost": cost,
        "fees": fees,
        "gap": 1.0 - cost - fees,         # profit per pair
        "pairs_at_top": min(q[3] for q in quotes),
    }


def check_pair_window(market):
    """Up + Down in one 5-minute window."""
    up, down = token_ids(market)
    return price_pair((market, up), (market, down))


def check_nested_pair(outer, inner):
    """YES on the bigger event + NO on the smaller one.

    outer: lower strike / later deadline (the bigger event)
    inner: higher strike / earlier deadline (inside it)
    If inner resolves YES, outer does too: you collect $1.
    If neither does, NO on inner pays $1. In between, both pay.
    """
    return price_pair((outer, token_ids(outer)[0]),
                      (inner, token_ids(inner)[1]))


def is_tradeable(opp, min_gap=0.005, min_pairs=10):
    """Only trade a gap that survives fees AND has size."""
    if opp["gap"] < min_gap:
        return False, f"gap after fees {opp['gap']:+.4f}"
    if opp["pairs_at_top"] < min_pairs:
        return False, (f"only {opp['pairs_at_top']:.0f} pairs "
                       f"at the best asks")
    return True, f"gap after fees {opp['gap']:+.4f}"


def validate_order_price(price, tick_size=0.01):
    """Round a limit price to the market's tick size."""
    return round(round(price / tick_size) * tick_size, 4)


def pre_flight_check(market, limit_price):
    """Gamma fields: negRisk, acceptingOrders, orderPriceMinTickSize."""
    if market.get("negRisk"):
        return False, "negRisk market: out of scope here"
    if market.get("closed") or not market.get("acceptingOrders"):
        return False, "market is not accepting orders"
    tick = float(market.get("orderPriceMinTickSize") or 0.01)
    return True, validate_order_price(limit_price, tick)


def execute_pair(opp, portfolio, max_cost=50.0):
    """Paper-buy both legs at their best asks and hold them."""
    pairs = int(min(opp["pairs_at_top"],
                    max_cost / (opp["cost"] + opp["fees"])))
    if pairs == 0:
        return False
    for leg in opp["legs"]:
        portfolio.buy(leg["token_id"], leg["ask"], pairs,
                      market_name=leg["market"]["question"][:40],
                      fee_rate=get_fee_rate(leg["market"]))
    print(f"  PAPER PAIR: {pairs} pairs | locked "
          f"${opp['gap'] * pairs:.2f} at resolution")
    return True


def run_arbitrage_scanner(min_gap=0.005, max_cost=50.0,
                          scan_interval=30, portfolio=None):
    # Chapter 9's portfolio manager passes in its own slice
    portfolio = portfolio or PaperPortfolio(starting_balance=500.0)
    checked = found = 0
    print("=== Arbitrage Scanner Started ===")
    print(f"Min gap after fees: ${min_gap:.3f} | "
          f"max ${max_cost:.0f} per pair trade")

    cycle = 0
    while True:
        cycle += 1
        ts = datetime.now().strftime("%H:%M:%S")
        settle_resolved_positions(portfolio)   # held pairs pay out here
        candidates = []
        for m in find_pair_windows():
            label = m["question"].split(" - ")[0]
            candidates.append(("WINDOW", label,
                               lambda m=m: check_pair_window(m)))
        for s in find_nested_sets():
            ms = s["markets"]
            name = s["title"].split(" on ")[0][:24]
            for i, (outer, inner) in enumerate(zip(ms, ms[1:])):
                if s["kind"] == "LADDER":
                    label = (f"{name} {outer['groupItemTitle']}/"
                             f"{inner['groupItemTitle']}")
                else:
                    label = f"{name} pair {i + 1}"
                candidates.append((s["kind"], label,
                                   lambda o=outer, n=inner:
                                   check_nested_pair(o, n)))

        best = {}                     # closest gap per kind
        for kind, label, check in candidates:
            try:
                opp = check()
            except requests.RequestException:
                continue
            if opp is None:
                continue                  # a side of a book is empty
            checked += 1
            if kind not in best or opp["gap"] > best[kind][1]["gap"]:
                best[kind] = (label, opp)
            ok, reason = is_tradeable(opp, min_gap)
            if ok:
                found += 1
                print(f"[{ts}] ARB {kind} {label} | {reason}")
                execute_pair(opp, portfolio, max_cost)

        print(f"[{ts}] cycle {cycle}: {len(candidates)} pairs listed")
        for kind, (label, opp) in best.items():
            print(f"  closest {kind:8} {label} | cost "
                  f"{opp['cost']:.3f} + fees {opp['fees']:.4f} | "
                  f"gap {opp['gap']:+.4f}")
        print(f"  total checked {checked} | tradeable {found}")
        time.sleep(scan_interval)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Paper arbitrage scanner. Presets (assumptions to test): "
                    "conservative 0.01/25/60, moderate 0.005/50/30 (default), "
                    "aggressive 0.002/100/15.")
    parser.add_argument("--min-gap", type=float, default=0.005,
                        help="smallest $ profit per pair after fees (default 0.005)")
    parser.add_argument("--max-cost", type=float, default=50.0,
                        help="$ cap per pair trade (default 50)")
    parser.add_argument("--scan-interval", type=int, default=30,
                        help="seconds between passes (default 30)")
    parser.add_argument("--balance", type=float, default=500.0,
                        help="paper bankroll in $ (default 500)")
    parser.add_argument("--log", default="arbitrage_trades.csv",
                        help="trade log for audit.py (default arbitrage_trades.csv)")
    args = parser.parse_args(argv)
    portfolio = PaperPortfolio(args.balance, log_file=args.log)
    try:
        run_arbitrage_scanner(args.min_gap, args.max_cost, args.scan_interval, portfolio)
    except KeyboardInterrupt:
        print(f"\nStopped. Paper fills are in {args.log}")


if __name__ == "__main__":
    main()
