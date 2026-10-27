"""One test per entry in BOOK-FIXES.md: the behaviour the chapter's prose
describes, which the printed code didn't deliver."""
import contextlib
import io
import json
import unittest
from unittest import mock

import requests

from support import InScratchDir, Response, market

import audit
import claude_agent
import contrarian
import resolution
import trainer
from trainer import PaperPortfolio


class MaxDrawdownIsTheLargestDrop(unittest.TestCase):
    def test_recovery_keeps_the_max(self):
        pf = PaperPortfolio(100.0)
        pf.buy("tok", 0.50, 100)
        self.assertAlmostEqual(pf.stats({"tok": 0.40})["max_drawdown"], 10.0)
        after = pf.stats({"tok": 0.50})
        self.assertAlmostEqual(after["max_drawdown"], 10.0)
        self.assertAlmostEqual(after["drawdown"], 0.0)

    def test_agent_gate_uses_the_drop_right_now(self):
        pf = PaperPortfolio(100.0)
        pf.buy("tok", 0.50, 100)
        pf.stats({"tok": 0.40})            # was down 20% from the peak
        pf.stats({"tok": 0.50})            # and recovered
        ok, problems = claude_agent.validate_trade(
            {"amount_usd": 10, "side": "BUY_YES"}, pf)
        self.assertTrue(ok, problems)


class CategoryWordsMatchWholeWords(unittest.TestCase):
    def test_the_bad_matches(self):
        self.assertEqual(contrarian.classify_market("Will the Netherlands win the World Cup?"), "sports")
        self.assertEqual(contrarian.classify_market("Will the bill resolve the shutdown?"), "policy_announcement")
        self.assertEqual(contrarian.classify_market("Will Sevilla win?"), "sports")
        self.assertEqual(contrarian.classify_market("Will Bethesda release a new game?"), "other")
        self.assertNotEqual(contrarian.extract_primary_keyword("Will the Netherlands win?"), "ethereum")

    def test_coins_still_count(self):
        for q in ["Will Ethereum hit $5,000?", "Will SOL close above $200?", "Will a cryptocurrency ETF launch?"]:
            self.assertEqual(contrarian.classify_market(q), "crypto_price", q)

    def test_docstring_examples(self):
        examples = {"Will BTC close above $100K on Friday?": "crypto_price",
                    "Will the Fed cut rates in June?": "fed_rate",
                    "Will Company X beat Q4 earnings?": "earnings_beat",
                    "Will Trump win the election?": "politics",
                    "Will Team X win the Super Bowl?": "sports",
                    "Will [Celebrity] file to run?": "other",
                    "Will GDP growth exceed 3%?": "economic_data"}
        for q, want in examples.items():
            self.assertEqual(contrarian.classify_market(q), want, q)
        self.assertEqual(contrarian.extract_primary_keyword("Will BTC close above $100K?"), "bitcoin")
        self.assertEqual(contrarian.extract_primary_keyword("Will the Fed cut rates?"), "federal reserve")
        self.assertEqual(contrarian.extract_primary_keyword("Will Company X beat earnings?"), "company x")

    def test_neutral_headlines_score_neutral(self):
        heads = ["Bitcoin price steady now as traders wait", "Analysts know little about the economy",
                 "Fed nominee announcement due Tuesday", "New blockchain upgrade ships",
                 "All eyes on the jobs data"]
        tweets = [{"text": h, "public_metrics": {"like_count": 1, "retweet_count": 0}} for h in heads * 2]
        self.assertEqual(contrarian.sentiment_score("x", tweets, min_tweets=5), 0.50)


class ContrarianFadesOnlyABullishCrowd(unittest.TestCase):
    def _scan(self, sentiment):
        bot = contrarian.ContrarianBot(PaperPortfolio(500.0))
        m = market("Will Team X win the final?")
        kw = contrarian.extract_primary_keyword(m["question"])
        with mock.patch.object(contrarian, "get_book", return_value=([(0.79, 100)], [(0.81, 100)])):
            return bot.scan([m], {kw: sentiment})

    def test_bearish_crowd_is_not_faded_by_buying_no(self):
        self.assertEqual(self._scan(0.05), [])

    def test_bullish_crowd_is(self):
        opps = self._scan(0.95)
        self.assertEqual([o["direction"] for o in opps], ["SELL_YES"])


class ResolutionSeesSettlementAndNoTokens(InScratchDir):
    def test_both_tokens_indexed(self):
        m = market(yes="YES1", no="NO1")
        idx = resolution.index_by_token([m])
        self.assertIs(idx["NO1"], m)

    def test_no_position_in_settled_market_is_queued(self):
        settled = market(yes="YES1", no="NO1", umaResolutionStatus="resolved", closed=True)
        pf = PaperPortfolio(100.0)
        pf.buy("NO1", 0.40, 10)
        with contextlib.redirect_stdout(io.StringIO()):
            q = resolution.check_redemption_queue(pf, resolution.MarketStateMonitor(),
                                                  resolution.index_by_token([settled]))
        self.assertEqual([x["token_id"] for x in q], ["NO1"])

    def test_market_data_is_reread_every_pass(self):
        calls = {"n": 0}

        def load():
            calls["n"] += 1
            return [], []
        sleeps = {"n": 0}

        def stop_after_three(_):
            sleeps["n"] += 1
            if sleeps["n"] == 3:
                raise KeyboardInterrupt
        with mock.patch.object(resolution, "load_markets", load), \
             mock.patch.object(resolution.EventDetector, "check_price", return_value=None), \
             mock.patch.object(resolution, "refresh_held"), \
             mock.patch.object(resolution, "settle_resolved_positions"), \
             mock.patch.object(resolution.time, "sleep", stop_after_three), \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                resolution.run_resolution_scanner()
        self.assertEqual(calls["n"], 3)


class PaperPositionsSettle(InScratchDir):
    def _settle(self, payload):
        pf = PaperPortfolio(100.0, log_file="t.csv")
        pf.buy("YES1", 0.60, 10)
        with mock.patch.object(trainer.requests, "get", return_value=Response(payload)) as get, \
             contextlib.redirect_stdout(io.StringIO()):
            trainer.settle_resolved_positions(pf)
        # Gamma leaves closed markets out of a token lookup unless asked
        self.assertEqual(get.call_args.kwargs["params"]["closed"], "true")
        return pf

    def test_winning_leg_pays_a_dollar(self):
        pf = self._settle([market(yes="YES1", no="NO1", closed=True, umaResolutionStatus="resolved",
                                  outcomePrices='["1", "0"]')])
        self.assertEqual(pf.positions, {})
        self.assertAlmostEqual(pf.cash, 100.0 - 6.0 + 10.0)
        self.assertEqual(pf.trade_log[-1]["action"], "SELL")
        with open("t.csv") as f:
            self.assertIn(",SELL,", f.read())

    def test_losing_leg_pays_nothing(self):
        pf = self._settle([market(yes="YES1", no="NO1", closed=True, umaResolutionStatus="resolved",
                                  outcomePrices='["0", "1"]')])
        self.assertAlmostEqual(pf.trade_log[-1]["pnl"], -6.0)

    def test_open_market_is_left_alone(self):
        pf = self._settle([market(yes="YES1", no="NO1", outcomePrices='["0.6", "0.4"]')])
        self.assertIn("YES1", pf.positions)


class ContrarianSurvivesAClosedBook(unittest.TestCase):
    def test_manage_exits_skips_a_404(self):
        pf = PaperPortfolio(500.0)
        pf.buy("NO1", 0.45, 10)
        bot = contrarian.ContrarianBot(pf)
        with mock.patch.object(contrarian, "get_book", side_effect=requests.HTTPError("404")):
            bot.manage_exits()
        self.assertIn("NO1", pf.positions)


class AgentLogsAndReportsWhatHappened(InScratchDir):
    def setUp(self):
        super().setUp()
        claude_agent.AGENT_LOG = "agent_log.jsonl"

    def _trade(self, side, amount, held=47):
        pf = PaperPortfolio(100.0)
        pf.buy("YES1", 0.50, held)
        m = market(yes="YES1", no="NO1")
        with mock.patch.object(claude_agent, "market_for_token", return_value=m), \
             mock.patch.object(claude_agent, "get_book", return_value=([(0.50, 500)], [(0.52, 500)])):
            return pf, claude_agent.execute_tool(
                "paper_trade", {"token_id": "YES1", "side": side, "amount_usd": amount,
                                "reasoning": "made-up reasoning"}, pf, None)

    def test_sell_reports_shares_sold(self):
        pf, r = self._trade("SELL_YES", 25)
        self.assertEqual(r["shares"], 47)
        self.assertEqual(pf.trade_log[-1]["shares"], 47)

    def test_reasoning_is_written_before_the_flag_says_so(self):
        _, r = self._trade("SELL_YES", 25)
        self.assertTrue(r["reasoning_logged"])
        with open("agent_log.jsonl") as f:
            rows = [json.loads(line) for line in f]
        self.assertEqual(rows[-1]["reasoning"], "made-up reasoning")


class AgentCostFollowsTheModel(InScratchDir):
    def _cycle(self, model, prices=None):
        class Usage:
            input_tokens, output_tokens = 10_000, 1_000

        class Reply:
            stop_reason, usage, content = "end_turn", Usage(), []

        class Client:
            def __init__(self, *a, **k):
                self.messages = self

            def create(self, **kw):
                return Reply()
        claude_agent.AGENT_LOG = "agent_log.jsonl"
        with mock.patch.object(claude_agent.anthropic, "Anthropic", Client), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            claude_agent.run_trading_agent(PaperPortfolio(100.0), None, model, prices)
        return out.getvalue()

    def test_known_model(self):
        self.assertIn("Cycle cost: $0.0300", self._cycle("claude-sonnet-5-5"))

    def test_swapped_model_without_a_price(self):
        out = self._cycle("claude-haiku-5-5")
        self.assertIn("Cycle cost: unknown, you must observe this", out)
        self.assertNotIn("$0.0300", out)

    def test_swapped_model_with_its_prices(self):
        self.assertIn("Cycle cost: $0.0015", self._cycle("claude-haiku-5-5", (0.10, 0.50)))


class AuditReadsTheLogYouName(InScratchDir):
    def test_path_argument(self):
        with open("arbitrage_trades.csv", "w") as f:
            for i in range(60):
                f.write(f"t,SELL,made-up,$0.50,10,${0.98 if i < 38 else -0.76:.2f}\n")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            audit.main(["arbitrage_trades.csv"])
        self.assertIn("Closed trades: 60", out.getvalue())

    def test_missing_log_is_a_verdict_not_a_crash(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            audit.main([])
        self.assertIn("too few trades to judge (0 closed)", out.getvalue())


class Chapter2Placement(unittest.TestCase):
    def test_min_spread_lives_in_scanner(self):
        from scanner import min_spread_for_breakeven
        self.assertEqual(min_spread_for_breakeven(0.50, 0.07, "maker"), 0)

    def test_stream_takes_a_token_id(self):
        import stream
        with mock.patch.object(stream.asyncio, "run") as run:
            stream.main(["123456"])
        run.assert_called_once()
        self.assertEqual(run.call_args[0][0].cr_frame.f_locals["token_id"], "123456")
        run.call_args[0][0].close()


if __name__ == "__main__":
    unittest.main()
