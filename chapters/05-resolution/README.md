# Chapter 5: the resolution scanner

Run it with four hours of strikes the first time, so it has something to look at:

```bash
cd polymarket-bots
python resolution.py 240
```

```
News path: off, no NEWS_API_KEY. The price path runs without it.
=== Resolution Scanner Started ===
Min edge: 10% | ladder strikes watched: 117
  SKIP above $82,400, 49 min left: no two-sided book
  SKIP above $81,000, 49 min left: NO at $0.999, net edge $-0.009 < $0.10
  SKIP above $80,800, 49 min left: BTC $80,178 inside the 1% buffer
  SKIP above $79,200, 49 min left: no two-sided book
```

Every line is a trade it refused, with the reason. A scanner like this mostly refuses. That's normal. Then run the defaults (last 10 minutes, 10% minimum edge, 1% buffer, $100 a trade, 100 bps dispute drag) across a daily ladder close at noon Eastern:

```bash
python resolution.py
```

You're done when it has classified real strikes in their last minutes and, for each one with a two-sided book, computed the edge after fees and dispute drag and either paper-traded it or printed the SKIP reason. The redemption check should have run once too. It runs every 5 minutes.

## Changing what it trades

| Flag | Default | What it moves |
|---|---|---|
| `minutes` (first argument) | 10 | how close to the close a strike must be |
| `--min-edge` | 0.10 | the net $ per share it needs |
| `--proxy-buffer` | 0.01 | how far BTC must sit from the strike, since CoinGecko isn't the Binance close that settles the market |
| `--position-size` | 100 | $ per trade (half that in an ELIGIBLE market) |
| `--balance` | 1000 | the paper bankroll |

The news path switches on when `NEWS_API_KEY` is set. A free NewsAPI key returns articles 24 hours late, so `is_stale()` throws every one away; it is good for testing the matching code and nothing else.

Paper fills go to `resolution_trades.csv`. A held position closes at its payout once the market resolves, listed first as `TO REDEEM:` and then `SETTLED:`. On a live wallet that step is `redeem_positions()`.
