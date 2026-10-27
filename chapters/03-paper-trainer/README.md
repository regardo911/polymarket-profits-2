# Chapter 3: the paper trainer

**Start here.** It's the test rig every later bot runs through.

```bash
cd polymarket-bots
python trainer.py
```

It takes the busiest 100 markets, keeps the five that moved most in the last hour with a two-sided book priced between 10 and 90 cents, and checks them every minute. When a price falls 3% below its 20-point average it buys $10 of YES at the ask; 3% above, it sells at the bid. Both sides pay the market's own taker fee. Every fill goes to `trades.csv`.

The first 15 minutes of a real run (NBA markets that evening):

```
=== Paper Trading Bot Started ===
Starting balance: $100.00
Monitoring 5 markets
Position size: $10.00
Trade log: trades.csv

[18:12:49] BUY 40 @ $0.25 | Spread: Cavaliers (-3.5)
[18:17:55] BUY 18 @ $0.55 | Timberwolves vs. Cavaliers
[18:17:55] SELL @ $0.23 | P&L: $-0.80 | Spread: Cavaliers (-3.5)

--- Cycle 10 Status ---
Portfolio: $99.02 (-1.0%)
Cash: $89.30 | Trades: 1 | Win rate: 0%
Max drawdown: 1.0%

[18:21:59] SELL @ $0.66 | P&L: $+1.98 | Timberwolves vs. Cavaliers
[18:21:59] BUY 62 @ $0.16 | Spread: Cavaliers (-3.5)
```

Paper trades, no money moved. The strategy is the book's deliberately dumb one, so these numbers say the pipeline works and nothing about an edge.

## Did it work?

Let it run 30 minutes, then check two things:

1. BUY and SELL lines appeared, and a status block every 10 cycles. `Trades:` counts closed trades only.
2. `trades.csv` holds a row per fill, and `python audit.py` reads it. With few trades it answers `too few trades to judge`, which is correct.

No BUY line after 30 minutes means your five markets sat inside the 3% band. Stop it (Ctrl+C) and start it again: it picks whatever is moving now.

## Make it yours

The chapter asks you to change three things before Chapter 4:

```bash
python trainer.py --balance 500 --position-size 50     # size it like the money you plan to use
python trainer.py --markets 20                         # watch more markets
python trainer.py --buy-threshold -0.05 --sell-threshold 0.05
```

Change one setting per run and let each run finish; comparing half a run on one setting with half on another tells you nothing. Pick deep markets: a $5,000 top level and a spread under $0.03 give paper fills that look like real ones.

Paper fills are at the best price with no slippage. Treat a paper profit as the most the same trades could make.

`trainer.py` is the finished Chapter 10 version: `log_trade()` is called after every buy and sell, and `PaperPortfolio` also keeps the largest drawdown (see [BOOK-FIXES.md](../../BOOK-FIXES.md)).
