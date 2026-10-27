"""The book's worked numbers, replayed through the code. If one of these fails,
the code no longer does what the chapter says it does."""
import contextlib
import io
import unittest
from unittest import mock

from support import InScratchDir, market

import arbitrage
import audit
import portfolio_manager
import resolution
import scanner
import utils
from trainer import PaperPortfolio


class Chapter2Fees(unittest.TestCase):
    def test_fee_examples(self):
        self.assertAlmostEqual(scanner.calculate_fee(500, 0.30, 0.07), 7.35)
        self.assertAlmostEqual(scanner.calculate_fee(100, 0.50, 0.07), 1.75)
        self.assertAlmostEqual(scanner.calculate_fee(500, 0.85, 0.07) / (500 * 0.85), 0.0105)

    def test_round_trip_breakeven(self):
        self.assertAlmostEqual(scanner.min_spread_for_breakeven(0.50, 0.07), 0.035)
        self.assertEqual(scanner.min_spread_for_breakeven(0.50, 0.07, execution="maker"), 0)

    def test_fee_rate_read_from_the_market(self):
        self.assertEqual(scanner.get_fee_rate({"feesEnabled": True, "feeSchedule": {"rate": 0.03}}), 0.03)
        self.assertEqual(scanner.get_fee_rate({"feesEnabled": False, "feeSchedule": {"rate": 0.07}}), 0.0)


class Chapter3Trainer(InScratchDir):
    def test_walkthrough_1_cash_trace(self):
        pf = PaperPortfolio(100.0)
        trace = []
        for shares, buy, sell in [(14, 0.68, 0.73), (22, 0.44, 0.42), (16, 0.61, 0.59)]:
            pf.buy("tok", buy, shares, fee_rate=0.07)
            trace.append(round(pf.cash, 2))
            pf.sell("tok", sell, fee_rate=0.07)
            trace.append(round(pf.cash, 2))
        self.assertEqual(trace, [90.27, 100.29, 90.23, 99.10, 89.07, 98.24])

    def test_sample_trade_pnl(self):
        pf = PaperPortfolio(100.0)
        pf.buy("tok", 0.68, 14, fee_rate=0.07)
        self.assertAlmostEqual(pf.trade_log[-1]["fee"], 0.2132, places=4)
        pf.sell("tok", 0.73, fee_rate=0.07)
        self.assertAlmostEqual(pf.trade_log[-1]["pnl"], 0.29, places=2)

    def test_walkthrough_1_reconciliation_assert(self):
        pf = PaperPortfolio(100.0)
        pf.buy("a", 0.50, 10, fee_rate=0.07)
        prices = {"a": 0.55}
        log_total = pf.cash + sum(pos["shares"] * prices.get(t, pos["avg_cost"])
                                  for t, pos in pf.positions.items())
        self.assertLess(abs(log_total - pf.total_value(prices)), 0.01)


class Chapter4Arbitrage(InScratchDir):
    def test_opening_example_loses_after_fees(self):
        books = {"UP": ([], [(0.48, 100)]), "DOWN": ([], [(0.49, 100)])}
        m = market(yes="UP", no="DOWN")
        with mock.patch.object(arbitrage, "get_book", side_effect=lambda t: books[t]):
            opp = arbitrage.check_pair_window(m)
        self.assertAlmostEqual(opp["cost"] + opp["fees"], 1.0050, places=4)
        self.assertFalse(arbitrage.is_tradeable(opp)[0])

    def test_walkthrough_2_locks_both_legs(self):
        books = {"UP": ([], [(0.28, 40)]), "DOWN": ([], [(0.67, 40)])}
        m = market(yes="UP", no="DOWN")
        with mock.patch.object(arbitrage, "get_book", side_effect=lambda t: books[t]):
            opp = arbitrage.check_pair_window(m)
        self.assertAlmostEqual(opp["gap"], 0.0204, places=4)
        self.assertTrue(arbitrage.is_tradeable(opp)[0])
        pf = PaperPortfolio(500.0)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            arbitrage.execute_pair(opp, pf, max_cost=9.80)
        self.assertEqual(pf.positions["UP"]["shares"], pf.positions["DOWN"]["shares"])
        self.assertEqual(pf.positions["UP"]["shares"], 10)
        self.assertIn("locked $0.20", out.getvalue())

    def test_tick_size(self):
        self.assertEqual(arbitrage.validate_order_price(0.6437, 0.01), 0.64)
        self.assertEqual(arbitrage.validate_order_price(0.6437, 0.001), 0.644)
        self.assertFalse(arbitrage.pre_flight_check(market(negRisk=True), 0.5)[0])


class Chapter5Edge(unittest.TestCase):
    def test_yes_at_67_cents(self):
        edge = resolution.calculate_edge({"ask": 0.67, "bid": 0.66}, "YES")
        self.assertAlmostEqual(edge["per_share_fee"], 0.0155, places=4)
        self.assertAlmostEqual(edge["per_share_dispute_loss"], 0.0067, places=4)
        self.assertAlmostEqual(edge["net_edge"], 0.3078, places=4)
        self.assertAlmostEqual(edge["net_edge"] * 500, 153.90, places=1)

    def test_political_dispute_drag(self):
        edge = resolution.calculate_edge({"ask": 0.67, "bid": 0.66}, "YES", dispute_risk_bps=500)
        self.assertAlmostEqual(edge["net_edge"] * 500, 140.50, places=1)

    def test_unknown_direction_never_trades(self):
        self.assertIsNone(resolution.calculate_edge({"ask": 0.5, "bid": 0.5}, "UNKNOWN"))


class Chapter9Rebalance(unittest.TestCase):
    def test_bounds_and_reserve(self):
        pm = portfolio_manager.PortfolioManager(1000.0)
        for name, pct in [("arbitrage", .25), ("resolution", .20), ("copy", .15),
                          ("claude", .10), ("contrarian", .15)]:
            pm.add_strategy(name, None, pct)
        # made-up daily returns: one standout, one flat, three middling
        made_up = {"arbitrage": [0.02, 0.021, 0.019, 0.02, 0.022, 0.018, 0.02],
                   "resolution": [0.0] * 7,
                   "copy": [0.01, -0.01, 0.012, -0.008, 0.01, -0.01, 0.011],
                   "claude": [0.005, 0.0, 0.004, 0.001, 0.006, 0.0, 0.003],
                   "contrarian": [0.03, -0.02, 0.025, -0.015, 0.02, -0.01, 0.01]}
        for name, rs in made_up.items():
            pm.strategies[name]["daily_returns"] = [{"date": f"d{i}", "return": r} for i, r in enumerate(rs)]
        pm.rebalance()
        for pct in pm.allocations.values():
            self.assertGreaterEqual(pct, 0.05 - 1e-9)
            self.assertLessEqual(pct, 0.35 + 1e-9)
        self.assertAlmostEqual(sum(pm.allocations.values()), 0.85)
        self.assertGreaterEqual(pm.reserve, 0.15 * 1000.0 - 1e-6)


class Chapter10Sizing(InScratchDir):
    def test_kelly(self):
        full = utils.kelly_size(1.0, 0.60, 0.50, fraction=1.0)
        self.assertAlmostEqual(full, 0.1812, places=4)
        self.assertAlmostEqual(utils.kelly_size(1.0, 0.52, 0.50, fraction=1.0), 0.017, places=3)
        self.assertEqual(utils.kelly_size(1.0, 0.60, 0.65), 0)
        self.assertAlmostEqual(utils.kelly_size(5000, 0.60, 0.50, fraction=1.0), 905.8, places=1)

    def test_streaks(self):
        self.assertAlmostEqual(audit.streak_probability(65, 100), 0.0018, places=4)
        self.assertAlmostEqual(audit.streak_probability(20, 30), 0.0494, places=4)

    def test_other_sizers(self):
        self.assertEqual(utils.fixed_fraction_size(1000), 20)
        self.assertAlmostEqual(utils.edge_based_size(1, 0.02), 0.04)
        self.assertAlmostEqual(utils.edge_based_size(1, 0.05), 0.05)
        self.assertTrue(utils.check_stop_loss({"avg_cost": 0.62}, 0.52)[0])
        self.assertFalse(utils.check_stop_loss({"avg_cost": 0.62}, 0.55)[0])

    def test_the_60_trade_audit(self):
        # made-up log with the chapter's shape: 38 wins of $0.98, 22 losses of $0.76
        with open("trades.csv", "w") as f:
            for i in range(60):
                f.write(f"2026-01-01T00:00:00,SELL,made-up-tok,$0.50,10,${0.98 if i < 38 else -0.76:.2f}\n")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            audit.audit()
        self.assertEqual(out.getvalue().splitlines(), [
            "Closed trades: 60",
            "Win rate: 63% | avg win $0.98 | avg loss $-0.76",
            "Win/loss ratio: 1.29 (aim for 1.5+)",
            "EV per trade: $+0.34",
            "Kelly from this log: 34.9% | quarter-Kelly: 8.7%",
            "Chance of 38+ wins in 60 by luck: 2.6%",
            "VERDICT: positive EV that luck alone rarely produces.",
        ])


class Chapter12Decay(unittest.TestCase):
    def test_decay_alert(self):
        log = [{"pnl": -0.15}] * 30 + [{"pnl": -0.30}] * 30
        d = utils.detect_edge_decay(log)
        self.assertAlmostEqual(d["decay_pct"], 100.0)
        self.assertTrue(d["alert"])
        self.assertIsNone(utils.detect_edge_decay(log[:59]))

    def test_platform_change_first_sighting_is_silent(self):
        mon = utils.PlatformChangeMonitor()
        m = market(feeSchedule={"rate": 0.03})
        self.assertEqual(mon.check_for_changes([m]), [])
        changed = mon.check_for_changes([market(feeSchedule={"rate": 0.05})])
        self.assertEqual([(c["field"], c["previous"], c["current"]) for c in changed],
                         [("feeSchedule.rate", 0.03, 0.05)])


if __name__ == "__main__":
    unittest.main()
