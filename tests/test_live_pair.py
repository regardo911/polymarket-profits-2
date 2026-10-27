"""live_pair.py: off unless the code turns it on, and the fill check and unwind
do the right thing. The SDK clients here are fakes; no account is touched."""
import contextlib
import io
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

import requests

from support import InScratchDir, Response, market

import live_pair

UP, DOWN = "UPTOKEN000001", "DOWNTOKEN0001"


def fake_get(books, blocked=False, seen=None):
    """Answers only the public calls a dry run is allowed to make."""
    window = market("Bitcoin Up or Down - made-up window", yes=UP, no=DOWN)

    def get(url, params=None, timeout=None):
        (seen if seen is not None else []).append(url)
        if url == live_pair.GEOBLOCK:
            return Response({"blocked": blocked, "country": "XX"})
        if "/events/slug/" in url:
            return Response({"markets": [window]})
        if url.endswith("/book"):
            bids, asks = books[params["token_id"]]
            return Response({"bids": [{"price": str(p), "size": str(s)} for p, s in bids],
                             "asks": [{"price": str(p), "size": str(s)} for p, s in asks]})
        raise AssertionError(f"dry run called an unexpected url: {url}")
    return get


CHEAP = {UP: ([(0.27, 50)], [(0.28, 50)]), DOWN: ([(0.66, 50)], [(0.67, 50)])}
DEAR = {UP: ([(0.47, 50)], [(0.48, 50)]), DOWN: ([(0.48, 50)], [(0.49, 50)])}


def no_writes(*a, **k):
    raise AssertionError("a dry run must never send a POST")


class OffByDefault(InScratchDir):
    def tearDown(self):
        live_pair.LIVE_MODE = False
        super().tearDown()

    def run_main(self, argv, books=CHEAP, blocked=False, seen=None):
        with mock.patch.object(requests, "get", fake_get(books, blocked, seen)), \
             mock.patch.object(requests, "post", no_writes), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            code = live_pair.main(argv)
        return code, out.getvalue()

    def test_dry_run_prints_both_orders_and_exits_0(self):
        code, out = self.run_main([])
        self.assertEqual(code, 0)
        self.assertIn("leg 1  BUY", out)
        self.assertIn("leg 2  BUY", out)
        self.assertIn("gap after fees +0.0204", out)
        self.assertIn("DRY RUN: no order sent", out)
        self.assertNotIn("polymarket", sys.modules)      # the live SDK was never imported

    def test_only_public_calls(self):
        seen = []
        self.run_main([], seen=seen)
        self.assertTrue(seen)
        for url in seen:
            self.assertTrue(url == live_pair.GEOBLOCK or "gamma-api.polymarket.com" in url
                            or url == "https://clob.polymarket.com/book", url)

    def test_no_flag_turns_it_on(self):
        for flag in (["--live"], ["--live-mode"], ["--confirm", "I_HAVE_REVIEWED_THIS"]):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                live_pair.main(flag)
        with mock.patch.dict(os.environ, {"LIVE_MODE": "1", "POLYMARKET_LIVE": "true"}):
            code, out = self.run_main([])
        self.assertIn("DRY RUN", out)
        self.assertFalse(live_pair.LIVE_MODE)

    def test_switch_needs_the_exact_confirm_string(self):
        with self.assertRaises(PermissionError):
            live_pair.set_live_mode(True)
        with self.assertRaises(PermissionError):
            live_pair.set_live_mode(True, confirm="yes")
        self.assertFalse(live_pair.LIVE_MODE)

    def test_size_cap(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            live_pair.main(["--max-usd", "500"])

    def test_live_refuses_a_blocked_country(self):
        live_pair.set_live_mode(True, confirm=live_pair.CONFIRM)
        code, out = self.run_main([], blocked=True)
        self.assertEqual(code, 3)
        self.assertIn("REFUSED", out)
        self.assertNotIn("polymarket", sys.modules)

    def test_live_refuses_a_gap_that_fees_eat(self):
        live_pair.set_live_mode(True, confirm=live_pair.CONFIRM)
        code, out = self.run_main([], books=DEAR)
        self.assertEqual(code, 3)
        self.assertIn("REFUSED", out)

    def test_live_us_refuses_until_fees_are_set(self):
        live_pair.set_live_mode(True, confirm=live_pair.CONFIRM)
        code, out = self.run_main(["--platform", "us", "--us-leg", "made-up-a", "long", "0.40",
                                   "--us-leg", "made-up-b", "short", "0.55"])
        self.assertEqual(code, 3)
        self.assertIn("US_FEE_PER_PAIR", out)


class FakeSecureClient:
    """Duck-types the polymarket-client calls live_pair uses."""

    def __init__(self, fills):
        self.fills, self.calls, self.n = fills, [], 0

    def place_limit_order(self, **kw):
        self.calls.append(("place_limit_order", kw))
        self.n += 1
        return SimpleNamespace(ok=True, order_id=f"order{self.n}", status="live")

    def get_order(self, order_id):
        return SimpleNamespace(size_matched=str(self.fills[order_id]), status="live")

    def cancel_order(self, order_id):
        self.calls.append(("cancel_order", order_id))

    def place_market_order(self, **kw):
        self.calls.append(("place_market_order", kw))
        return SimpleNamespace(ok=True, order_id="unwind1", status="matched")


def plan(size=10):
    return {"pairs": size, "orders": [
        {"label": "up", "token_id": UP, "side": "BUY", "price": 0.28, "size": size},
        {"label": "down", "token_id": DOWN, "side": "BUY", "price": 0.67, "size": size}]}


class FillCheckAndUnwind(unittest.TestCase):
    def run_pair(self, fills):
        client = FakeSecureClient(fills)
        with mock.patch.object(live_pair, "get_book", return_value=([(0.26, 100)], [(0.30, 100)])), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            filled = live_pair.run_pair(live_pair.InternationalLegs(client), plan(), wait_s=0)
        return client.calls, filled, out.getvalue()

    def test_both_legs_fill(self):
        calls, filled, out = self.run_pair({"order1": 10, "order2": 10})
        self.assertEqual(filled, (10.0, 10.0))
        self.assertEqual([c[0] for c in calls], ["place_limit_order", "place_limit_order"])
        self.assertIn("both legs filled", out)

    def test_one_leg_fills(self):
        calls, filled, out = self.run_pair({"order1": 10, "order2": 0})
        names = [c[0] for c in calls]
        self.assertEqual(names, ["place_limit_order", "place_limit_order", "cancel_order",
                                 "place_market_order"])
        self.assertEqual(calls[2][1], "order2")
        sell = calls[-1][1]
        self.assertEqual((sell["token_id"], sell["side"], sell["shares"], sell["min_price"],
                          sell["order_type"]), (UP, "SELL", 10.0, 0.26, "FAK"))
        self.assertIn("ONE-LEGGED", out)

    def test_partial_fills_sell_only_the_unpaired_shares(self):
        calls, filled, out = self.run_pair({"order1": 6, "order2": 10})
        sell = calls[-1][1]
        self.assertEqual((sell["token_id"], sell["shares"]), (DOWN, 4.0))

    def test_rejected_leg_counts_as_unfilled(self):
        client = FakeSecureClient({"order1": 10})
        client.place_limit_order = lambda **kw: (client.calls.append(("place_limit_order", kw)) or
                                                 (SimpleNamespace(ok=True, order_id="order1", status="live")
                                                  if kw["token_id"] == UP else
                                                  SimpleNamespace(ok=False, code="unmatched", message="made up")))
        with mock.patch.object(live_pair, "get_book", return_value=([(0.26, 100)], [])), \
             contextlib.redirect_stdout(io.StringIO()):
            filled = live_pair.run_pair(live_pair.InternationalLegs(client), plan(), wait_s=0)
        self.assertEqual(filled, (10.0, 0.0))
        self.assertEqual(client.calls[-1][0], "place_market_order")


class FakeUSClient:
    def __init__(self, fills):
        self.fills, self.calls, self.n = fills, [], 0
        self.orders = self

    def create(self, params):
        self.calls.append(("create", params))
        self.n += 1
        return {"id": f"us{self.n}"}

    def retrieve(self, order_id):
        return {"order": {"cumQuantity": self.fills.get(order_id, 0), "state": "ORDER_STATE_NEW"}}

    def cancel(self, order_id, params):
        self.calls.append(("cancel", order_id, params))


class USUnwind(unittest.TestCase):
    def test_one_leg_fills(self):
        live_pair.US_FEE_PER_PAIR = None
        p, _ = live_pair.plan_us([("made-up-a", "long", "0.40"), ("made-up-b", "short", "0.55")], 50)
        self.assertAlmostEqual(p["cost"], 0.85)
        client = FakeUSClient({"us1": p["pairs"]})
        with contextlib.redirect_stdout(io.StringIO()):
            live_pair.run_pair(live_pair.USLegs(client), p, wait_s=0)
        self.assertEqual(client.calls[2], ("cancel", "us2", {"marketSlug": "made-up-b"}))
        sell = client.calls[-1][1]
        self.assertEqual((sell["marketSlug"], sell["intent"], sell["type"], sell["quantity"]),
                         ("made-up-a", "ORDER_INTENT_SELL_LONG", "ORDER_TYPE_MARKET", p["pairs"]))


if __name__ == "__main__":
    unittest.main()
