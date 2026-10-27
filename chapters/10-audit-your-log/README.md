# Chapter 10: is it good, or just lucky?

`audit.py` reads the SELL rows of a trade log and gives you EV per trade, the win/loss ratio, a Kelly fraction and the chance the result is luck.

```bash
cd polymarket-bots
python audit.py                           # trades.csv, the trainer's log
python audit.py arbitrage_trades.csv      # any strategy's log
python audit.py *_trades.csv trades.csv   # one verdict per log
```

With nothing closed yet: `VERDICT: too few trades to judge (0 closed). Let the trainer run.` On a 60-trade log of 38 wins at $0.98 and 22 losses at $0.76 (the chapter's sample, made-up numbers) it prints:

```
Closed trades: 60
Win rate: 63% | avg win $0.98 | avg loss $-0.76
Win/loss ratio: 1.29 (aim for 1.5+)
EV per trade: $+0.34
Kelly from this log: 34.9% | quarter-Kelly: 8.7%
Chance of 38+ wins in 60 by luck: 2.6%
VERDICT: positive EV that luck alone rarely produces.
```

What to do with each verdict, from the chapter:

| Verdict | Your move |
|---|---|
| too few trades (under 50) | keep the bot running |
| negative EV | add the 15% stop-loss, rerun the trainer |
| ratio under 1.5 | tighten the stop or raise the take-profit |
| luck above 5% | keep paper trading until it drops |

Run it before every change to position size and before any real money goes in.

## Wiring the stop and the sizers

The trainer writes its log already (the chapter's Step 1 is done in `trainer.py`). The sizing functions are in `utils.py` for you to wire in; no bot calls them yet, which is the chapter's exercise:

```python
from utils import check_stop_loss, kelly_size
check_stop_loss({"avg_cost": 0.62}, 0.52)      # (True, 'Stop-loss triggered: ... loss 16.1%')
kelly_size(1000, win_prob=0.60, entry_price=0.50, fee_rate=0.07)   # 45.29: quarter-Kelly after fee and slippage
```

Pass each market's own `get_fee_rate()` and a slippage figure from your own fills. 0.07 and 0.005 are only the defaults.
