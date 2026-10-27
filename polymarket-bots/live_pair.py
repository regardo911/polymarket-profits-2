"""The guarded live pair: limit orders on both legs, a fill check, and the one-legged unwind. Chapter 4.

Chapter 4 prints no order-signing code; this is the live setup it points to.
As shipped it is a dry run: it prices the pair with price_pair(), prints the
two limit orders it WOULD send, and exits. Nothing touches an account.

    python live_pair.py                                  # the live BTC 5-minute window
    python live_pair.py --asset eth
    python live_pair.py --legs <YES token, bigger event> <NO token, smaller event>
    python live_pair.py --platform us --us-leg <slug> long 0.48 --us-leg <slug> short 0.55

Going live is an edit, never a flag: read this whole file, then uncomment the
set_live_mode(...) line at the bottom. Live mode also needs the SDK for where
you live, in its own virtual environment (requirements-live.txt says why),
and your own credentials in the environment:
  outside the US  polymarket-client: POLYMARKET_PRIVATE_KEY, optional
                  POLYMARKET_WALLET_ADDRESS (leave it out for a Deposit Wallet)
  in the US       polymarket-us: POLYMARKET_KEY_ID, POLYMARKET_SECRET_KEY
Live mode refuses a blocked country on the international path, a pair whose
gap after fees is under --min-gap, and any pair costing more than $200
(Chapter 4: start with $100-200). Never route around a block with a VPN.

SDK names used here were read from the installed packages, not guessed:
  polymarket-client 0.14.0 (docs.polymarket.com/getting-started/python.md and
  docs.polymarket.com/migrate/clob-sdk-to-unified-sdk.md):
    SecureClient.create(private_key=..., wallet=...)
    place_limit_order(token_id=, price=, size=, side="BUY")
        -> AcceptedOrder(ok, order_id, status in live|matched|delayed)
           or RejectedOrder(ok=False, code, message)
    get_order(order_id=) -> OpenOrder(status, size_matched, original_size)
    cancel_order(order_id=)                       (off chain, through the CLOB API)
    place_market_order(token_id=, side="SELL", shares=, min_price=, order_type="FAK")
  polymarket-us 2.4.1 (docs.polymarket.us/getting-started/quickstart.md and
  docs.polymarket.us/api-reference/sdks/python/orders.md):
    PolymarketUS(key_id=, secret_key=)
    orders.create({...}) -> {"id": ...}; orders.retrieve(id) -> {"order": {... "cumQuantity"}}
    orders.cancel(id, {"marketSlug": slug})
There is no FILLED status on polymarket.com: a leg counts as filled when
size_matched reaches the order size. Polymarket US does report
ORDER_STATE_FILLED, and the same cumQuantity test covers it.

It has been checked to install and load. It has never been run against a
real account here.
Educational software, not financial advice. Live trading can lose all the money you put in.
"""
import argparse
import os
import sys
import time

import requests

from scanner import get_book, token_ids
from arbitrage import find_pair_windows, is_tradeable, pre_flight_check, price_pair
from copy_trader import market_for_token

GEOBLOCK = "https://polymarket.com/api/geoblock"
CONFIRM = "I_HAVE_REVIEWED_THIS"
MAX_LIVE_USD = 200.0     # Chapter 4: "Start with $100-200 real capital"
# Polymarket US publishes its own fee schedule (docs.polymarket.us/fees.md).
# Put your $ fee per pair here before a live US run; live US refuses while None.
US_FEE_PER_PAIR = None

LIVE_MODE = False


def set_live_mode(enabled, confirm=""):
    """The only way to turn live orders on. Called from code, never from a flag."""
    global LIVE_MODE
    if enabled and confirm != CONFIRM:
        raise PermissionError(f'set_live_mode(True, confirm="{CONFIRM}") is the only way on')
    LIVE_MODE = bool(enabled)


def check_geoblock():
    """Chapter 4's check before any order code. Returns blocked and country only."""
    data = requests.get(GEOBLOCK, timeout=10).json()
    return {"blocked": bool(data.get("blocked")), "country": data.get("country")}


# ---------------------------------------------------------------- planning

def plan_international(legs=None, asset="btc", max_usd=100.0):
    """Two BUY limit orders at the best asks, sized like execute_pair()."""
    if legs:
        pair = [(market_for_token(t), t) for t in legs]
        if not all(m for m, _ in pair):
            return None, "a token id did not match an open Gamma market"
    else:
        windows = find_pair_windows((asset,))
        if not windows:
            return None, f"no live {asset} 5-minute window on Gamma right now"
        up, down = token_ids(windows[0])
        pair = [(windows[0], up), (windows[0], down)]
    opp = price_pair(*pair)
    if opp is None:
        return None, "one side of a book is empty"
    pairs = int(min(opp["pairs_at_top"], max_usd / (opp["cost"] + opp["fees"])))
    orders = []
    for leg in opp["legs"]:
        ok, price = pre_flight_check(leg["market"], leg["ask"])
        if not ok:
            return None, price
        orders.append({"label": leg["market"].get("question", "")[:50],
                       "token_id": leg["token_id"], "side": "BUY",
                       "price": price, "size": pairs})
    return {"orders": orders, "pairs": pairs, "cost": opp["cost"],
            "fees": opp["fees"], "gap": opp["gap"], "opp": opp}, None


def plan_us(us_legs, max_usd=100.0):
    """Two limit orders on Polymarket US. Every US price is the YES price
    (docs.polymarket.us/concepts/orders.md), so a short (NO) leg quoted at
    0.55 costs 1 - 0.55 = 0.45."""
    orders, cost = [], 0.0
    for slug, side, price in us_legs:
        price = float(price)
        cost += price if side == "long" else 1 - price
        orders.append({"label": slug, "slug": slug, "price": price,
                       "intent": "ORDER_INTENT_BUY_LONG" if side == "long"
                       else "ORDER_INTENT_BUY_SHORT", "side": f"BUY {side.upper()}"})
    fees = US_FEE_PER_PAIR
    pairs = int(max_usd / (cost + (fees or 0))) if cost > 0 else 0
    for o in orders:
        o["size"] = pairs
    gap = None if fees is None else 1.0 - cost - fees
    return {"orders": orders, "pairs": pairs, "cost": cost, "fees": fees, "gap": gap}, None


def print_plan(plan):
    for i, o in enumerate(plan["orders"], 1):
        where = o.get("token_id", o.get("slug", ""))
        print(f"  leg {i}  {o['side']} {o['size']} @ ${o['price']:.3f}  "
              f"{where[:14]}  {o['label']}")
    fees = "unknown (set US_FEE_PER_PAIR)" if plan["fees"] is None else f"{plan['fees']:.4f}"
    gap = "unknown until fees are set" if plan["gap"] is None else f"{plan['gap']:+.4f}"
    print(f"  per pair: cost {plan['cost']:.3f} + fees {fees} | gap after fees {gap}")
    print(f"  {plan['pairs']} pairs, about ${plan['pairs'] * plan['cost']:.2f} before fees")


# ---------------------------------------------------- the two SDK adapters

class InternationalLegs:
    """polymarket-client SecureClient calls, one leg at a time."""

    def __init__(self, client):
        self.client = client

    def place(self, order):
        r = self.client.place_limit_order(token_id=order["token_id"], price=order["price"],
                                          size=order["size"], side="BUY")
        if not r.ok:
            print(f"  REJECTED {order['label']}: {r.code} {r.message}")
            return None
        print(f"  placed {order['size']} @ ${order['price']:.3f}: {r.status} ({r.order_id})")
        return r.order_id

    def matched(self, order_id):
        return float(self.client.get_order(order_id=order_id).size_matched)

    def cancel(self, order_id):
        self.client.cancel_order(order_id=order_id)

    def sell(self, order, shares):
        bids, _ = get_book(order["token_id"])
        if not bids:
            print(f"  NO BID: you hold {shares:g} shares of {order['label']}. Close them by hand.")
            return
        r = self.client.place_market_order(token_id=order["token_id"], side="SELL",
                                           shares=shares, min_price=bids[0][0], order_type="FAK")
        print(f"  UNWIND: sell {shares:g} at ${bids[0][0]:.3f} or better: "
              + (f"{r.status} ({r.order_id})" if r.ok else f"rejected {r.code}"))


class USLegs:
    """polymarket-us PolymarketUS calls, one leg at a time."""

    def __init__(self, client):
        self.client = client

    def place(self, order):
        r = self.client.orders.create({
            "marketSlug": order["slug"], "intent": order["intent"],
            "type": "ORDER_TYPE_LIMIT",
            "price": {"value": f"{order['price']:.3f}", "currency": "USD"},
            "quantity": order["size"], "tif": "TIME_IN_FORCE_GOOD_TILL_CANCEL"})
        print(f"  placed {order['side']} {order['size']} @ ${order['price']:.3f}: {r.get('id')}")
        return r.get("id")

    def matched(self, order_id):
        return float(self.client.orders.retrieve(order_id)["order"].get("cumQuantity") or 0)

    def cancel(self, order_id):
        self.client.orders.cancel(order_id, {"marketSlug": self._slugs[order_id]})

    def sell(self, order, shares):
        intent = order["intent"].replace("BUY", "SELL")
        r = self.client.orders.create({
            "marketSlug": order["slug"], "intent": intent, "type": "ORDER_TYPE_MARKET",
            "quantity": shares, "tif": "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"})
        print(f"  UNWIND: {intent} {shares:g} at market: {r.get('id')}")


# -------------------------------------------- both legs, the check, the unwind

def run_pair(legs, plan, wait_s=20, poll_s=1.0):
    """Post both limit orders, wait for fills, then cancel and unwind whatever
    didn't pair up. Returns (filled leg 1, filled leg 2)."""
    ids = [legs.place(o) for o in plan["orders"]]
    if isinstance(legs, USLegs):
        legs._slugs = {i: o["slug"] for i, o in zip(ids, plan["orders"]) if i}
    size = plan["pairs"]

    def fills():
        return [legs.matched(i) if i else 0.0 for i in ids]

    deadline = time.time() + wait_s
    filled = fills()
    while min(filled) < size and time.time() < deadline:
        time.sleep(poll_s)
        filled = fills()

    if min(filled) >= size:
        print(f"FILL CHECK: both legs filled, {size} pairs locked. Hold to resolution, "
              f"then redeem (redeem_positions() in polymarket-client).")
        return tuple(filled)

    for i, f in zip(ids, filled):
        if i and f < size:
            legs.cancel(i)
    filled = fills()      # a fill can land between the last poll and the cancel
    print(f"FILL CHECK: leg 1 filled {filled[0]:g} of {size}, leg 2 filled {filled[1]:g} of {size}")
    extra = abs(filled[0] - filled[1])
    if extra:
        bigger = plan["orders"][0] if filled[0] > filled[1] else plan["orders"][1]
        print(f"ONE-LEGGED: {extra:g} unpaired shares are a bet, not arbitrage. Selling them now.")
        legs.sell(bigger, extra)
    print("Check your fills: GET https://data-api.polymarket.com/v2/trades?user=<your wallet>")
    return tuple(filled)


# ------------------------------------------------------------------- main

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Price one pair and print the two limit orders. Dry run unless "
                    "set_live_mode() is called in the code.")
    parser.add_argument("--platform", choices=["international", "us"], default="international")
    parser.add_argument("--asset", default="btc", help="5-minute window to price: btc or eth (default btc)")
    parser.add_argument("--legs", nargs=2, metavar="TOKEN",
                        help="two token ids: YES on the bigger event, NO on the smaller one")
    parser.add_argument("--us-leg", nargs=3, action="append", metavar=("SLUG", "long|short", "YES_PRICE"),
                        help="a Polymarket US leg; give exactly two")
    parser.add_argument("--max-usd", type=float, default=100.0,
                        help=f"$ cap on the pair trade (default 100, never above {MAX_LIVE_USD:.0f})")
    parser.add_argument("--min-gap", type=float, default=0.005,
                        help="live mode refuses a gap after fees below this (default 0.005)")
    parser.add_argument("--wait", type=int, default=20, help="seconds to wait for both fills (default 20)")
    args = parser.parse_args(argv)
    if args.max_usd > MAX_LIVE_USD:
        parser.error(f"--max-usd is capped at {MAX_LIVE_USD:.0f}")

    if args.platform == "us":
        if not args.us_leg or len(args.us_leg) != 2 or any(s not in ("long", "short") for _, s, _ in args.us_leg):
            parser.error("--platform us needs two --us-leg SLUG long|short YES_PRICE")
        plan, why = plan_us(args.us_leg, args.max_usd)
        geo = None
    else:
        geo = check_geoblock()
        plan, why = plan_international(args.legs, args.asset, args.max_usd)
    if plan is None:
        print(f"No pair to price: {why}")
        return 0

    print("Pair (Polymarket US):" if args.platform == "us" else "Pair:")
    print_plan(plan)
    tradeable = False
    if plan["gap"] is not None:
        if "opp" in plan:
            tradeable, reason = is_tradeable(plan["opp"], args.min_gap)
        else:
            tradeable = plan["gap"] >= args.min_gap and plan["pairs"] > 0
            reason = f"gap after fees {plan['gap']:+.4f}"
        print(f"  tradeable: {'yes' if tradeable else 'no'} ({reason})")
    if geo is not None:
        print(f"Geoblock: blocked={geo['blocked']} country={geo['country']}"
              + ("  -> the international live path refuses here; US readers use --platform us"
                 if geo["blocked"] else ""))

    if not LIVE_MODE:
        print("DRY RUN: no order sent. Live mode is off; turning it on is an edit at the bottom of this file.")
        return 0

    print("LIVE MODE: real orders on your own account. Not financial advice; you can lose all of it.")
    if geo is not None and geo["blocked"]:
        print("REFUSED: polymarket.com blocks new orders from your country.")
        return 3
    if plan["gap"] is None:
        print("REFUSED: set US_FEE_PER_PAIR from docs.polymarket.us/fees.md first.")
        return 3
    if not tradeable:
        print("REFUSED: the pair doesn't clear the Chapter 4 filter (gap after fees and depth).")
        return 3
    if plan["pairs"] * (plan["cost"] + plan["fees"]) > MAX_LIVE_USD:
        print(f"REFUSED: over the ${MAX_LIVE_USD:.0f} cap.")
        return 3

    if args.platform == "us":
        from polymarket_us import PolymarketUS
        client = PolymarketUS(key_id=os.environ["POLYMARKET_KEY_ID"],
                              secret_key=os.environ["POLYMARKET_SECRET_KEY"])
        run_pair(USLegs(client), plan, args.wait)
    else:
        from polymarket import SecureClient
        with SecureClient.create(private_key=os.environ["POLYMARKET_PRIVATE_KEY"],
                                 wallet=os.environ.get("POLYMARKET_WALLET_ADDRESS")) as client:
            run_pair(InternationalLegs(client), plan, args.wait)
    return 0


if __name__ == "__main__":
    # The one switch. Read every line above before you uncomment it.
    # set_live_mode(True, confirm="I_HAVE_REVIEWED_THIS")
    sys.exit(main())
