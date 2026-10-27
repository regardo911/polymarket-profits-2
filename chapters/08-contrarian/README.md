# Chapter 8: the contrarian bot

```bash
cd polymarket-bots
export NEWS_API_KEY=...       # optional; without it every score is 0.50 and the bot only scans
python contrarian.py
```

It buys NO only when four things line up: the price sits at least 10% above its category's base rate, the headlines run more than 85% bullish, the market isn't politics, policy, regulation or M&A (where the crowd may simply know), and the last big move was more than 10 minutes ago. It prints a line only when it trades (`CONTRARIAN: BUY NO ...`) or takes profit at +50% on its cost, so a quiet terminal for hours is normal. Run it overnight.

Without a key you'll see `No NEWS_API_KEY: every sentiment score is 0.50, so the bot scans and never trades.` That's expected: with no sentiment to go on, it scans and doesn't trade. A free NewsAPI key scores yesterday's coverage (its articles are 24 hours late), which is enough to watch the filters work.

The checkpoint is 3 or more markets where the crowd sat 10% or more above fair value, with the edge computed for each. Expect to wait.

## Your own base rates

The hardcoded rates (policy 0.70, sports 0.60 ...) are guesses. Build yours from resolved markets and use them:

```python
from contrarian import build_base_rates, FairValueEstimator
rates = build_base_rates(pages=10)     # prints e.g. "  sports: 10% YES (295 markets)"
estimator = FairValueEstimator()
estimator.base_rates = rates
```

Flags: `--min-edge` (0.10), `--max-position-pct` (0.08), `--scan-interval` (60), `--balance` (500). Fills go to `contrarian_trades.csv`, and a held position closes at its payout when the market resolves. Judge it after 50 closed trades with `python audit.py contrarian_trades.csv`: the study in this chapter found the cheap side loses on average, so your filtered trades have to beat that before any money goes in.
