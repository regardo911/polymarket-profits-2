"""Smoke runs of the commands the READMEs tell you to type, against made-up
API answers. They prove each command parses its flags, runs, and prints the
shape the chapter shows. They say nothing about live markets."""
import contextlib
import importlib
import io
import os
import unittest
from unittest import mock

import requests

from support import InScratchDir, Response, market

import audit
import portfolio_manager
import scanner
import trainer

COMMANDS = {
    "scanner": ["--top"], "watch": ["--markets", "--interval"], "stream": ["token_id"],
    "trainer": ["--balance", "--position-size", "--markets", "--log"],
    "arbitrage": ["--min-gap", "--max-cost", "--scan-interval"],
    "resolution": ["minutes", "--min-edge", "--position-size", "--proxy-buffer"],
    "copy_trader": ["wallet", "--period"], "copy_start": ["--target-wallet", "--target-value"],
    "claude_agent": ["--model", "--input-price"], "contrarian": ["--min-edge", "--scan-interval"],
    "portfolio_manager": ["--capital", "--target-wallet"], "audit": ["logs"],
    "verify_claim": ["wallet", "claimed_profit"], "live_pair": ["--platform", "--max-usd"],
}


class EveryCommandHasHelp(unittest.TestCase):
    def test_help_lists_the_flags(self):
        for name, flags in COMMANDS.items():
            module = importlib.import_module(name)
            with self.subTest(name), contextlib.redirect_stdout(io.StringIO()) as out, \
                    self.assertRaises(SystemExit) as done:
                module.main(["--help"])
            self.assertEqual(done.exception.code, 0)
            for flag in flags:
                self.assertIn(flag, out.getvalue(), f"{name} --help is missing {flag}")


def book_reply(bids, asks):
    return Response({"bids": [{"price": str(p), "size": "100"} for p in bids],
                     "asks": [{"price": str(p), "size": "100"} for p in asks]})


class TrainerFirstRun(InScratchDir):
    def test_paper_trades_land_in_trades_csv(self):
        mids = iter([0.50] * 6 + [0.47, 0.50, 0.50, 0.53] + [0.50] * 5)
        current = {"mid": 0.50}

        def get(url, params=None, timeout=None):
            if url.endswith("/markets"):
                return Response([market("Made-up market", yes="TOK1", no="TOK2", oneHourPriceChange=0.1)])
            m = current["mid"]
            return book_reply([round(m - 0.005, 3)], [round(m + 0.005, 3)])

        cycles = {"n": 0}

        def tick(_):
            cycles["n"] += 1
            current["mid"] = next(mids, None)
            if current["mid"] is None:
                raise KeyboardInterrupt
        with mock.patch.object(requests, "get", get), mock.patch.object(trainer.time, "sleep", tick), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            trainer.main(["--interval", "0"])
        text = out.getvalue()
        self.assertIn("=== Paper Trading Bot Started ===", text)
        self.assertIn("BUY", text)
        self.assertIn("SELL", text)
        with open("trades.csv") as f:
            actions = [line.split(",")[1] for line in f]
        self.assertEqual(actions, ["BUY", "SELL"])
        with contextlib.redirect_stdout(io.StringIO()) as verdict:
            audit.main([])
        self.assertIn("Closed trades: 1", verdict.getvalue())
        self.assertIn("too few trades to judge (need 50+)", verdict.getvalue())


class ScannerSnapshot(unittest.TestCase):
    def test_output_shape(self):
        def get(url, params=None, timeout=None):
            if url.endswith("/markets"):
                return Response([market("Made-up market A"), market("Made-up market B", yes="Y2", no="N2")])
            return book_reply([0.19, 0.18], [0.20, 0.21])
        with mock.patch.object(requests, "get", get), contextlib.redirect_stdout(io.StringIO()) as out:
            scanner.main([])
        text = out.getvalue()
        for line in ("Open markets fetched: 2", "Market 1: Made-up market A",
                     "taker fee rate: 0.07", "Spread: $0.0100", "Bid depth (top 3):"):
            self.assertIn(line, text)


class VerifyClaim(unittest.TestCase):
    def test_month_by_month(self):
        import verify_claim
        day = 86400
        points = [{"timestamp": 1_700_000_000 + i * day, "realized_pnl": 1000.0 * min(i, 40)}
                  for i in range(70)]
        replies = {"user-stats": {"trades": 1234, "all_time_pnl": {"realized_pnl": 40000.0, "deposits": None}},
                   "user-pnl": {"points": points},
                   "activity": [{"timestamp": 1_700_000_000 + 69 * day}]}

        def get(url, params=None, timeout=None):
            return Response({"data": replies[url.rsplit("/", 1)[1]]})
        with mock.patch.object(requests, "get", get), contextlib.redirect_stdout(io.StringIO()) as out:
            verify_claim.main(["0x" + "ab" * 20, "50000"])
        text = out.getvalue()
        self.assertIn("Trades on record:    1,234", text)
        self.assertIn("Deposits on record:  not reported", text)
        self.assertIn("Claim minus record:  $+10,000", text)
        self.assertIn("Month  1:", text)


class KeyAndWalletGates(unittest.TestCase):
    def test_copy_start_refuses_the_placeholder(self):
        import copy_start
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TARGET_WALLET", None)
            with self.assertRaises(SystemExit) as done:
                copy_start.main([])
        self.assertEqual(str(done.exception), "TARGET_WALLET is not a wallet address")

    def test_agent_says_what_it_needs(self):
        import claude_agent
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            with self.assertRaises(SystemExit) as done:
                claude_agent.main([])
        self.assertIn("your own Anthropic key", str(done.exception))


class Dashboard(unittest.TestCase):
    def test_prints_reserve_and_combined(self):
        pm = portfolio_manager.PortfolioManager(1000.0)
        pm.add_strategy("arbitrage", None, 0.25)
        pm.add_strategy("resolution", None, 0.20)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            portfolio_manager.portfolio_dashboard(pm)
            alerts = portfolio_manager.check_portfolio_health(pm)
        self.assertIn("Cash reserve: $550.00", out.getvalue())
        self.assertIn("COMBINED, reserve included: $1000.00 (+0.0%)", out.getvalue())
        self.assertEqual(alerts, [])


if __name__ == "__main__":
    unittest.main()
