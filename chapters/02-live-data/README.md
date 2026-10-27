# Chapter 2: live Polymarket data in your terminal

Three files, all in [`polymarket-bots/`](../../polymarket-bots): `scanner.py` (the helpers every later bot imports, plus a one-shot snapshot), `watch.py` (the 30-second spread loop) and `stream.py` (one token over the WebSocket). No key, no account.

```bash
cd polymarket-bots
python scanner.py
```

A real run, cut short:

```
=== Polymarket Live Data ===
Open markets fetched: 100

Market 8: Timberwolves vs. Cavaliers
  24h volume: $3,416,342 | taker fee rate: 0.0
  YES price: $0.430 bid / $0.440 ask
  Spread: $0.0100
  Bid depth (top 3):
    $0.430 - 37.97 shares
    $0.420 - 1,320.59 shares
    $0.410 - 392.64 shares
  Ask depth (top 3):
    ...
```

It worked if you see `Open markets fetched: 100` and at least one market with a bid, an ask and three depth levels. "One side of the book is empty" is a real answer too: on that run the five busiest markets, all Ballon d'Or markets, each had one side empty, so `python scanner.py --top 12` was needed to reach the two-sided books further down.

Then leave the loop running for a few minutes:

```bash
python watch.py                    # top 10 tightest spreads of the 50 busiest, every 30 s
python watch.py --markets 100 --interval 60
python stream.py                   # live Book/Trade lines for the busiest two-sided market
python stream.py <token_id>        # any token: the scanner's clobTokenIds
```

## Point it at your own markets

The busiest-100 list changes every day, so your output never matches the sample. What to check on any market you plan to trade: the spread, and the size at the best bid and ask. A $0.005 spread with 10 shares at the top is a price you can't trade at; Chapter 4's depth filter exists because of that.

`get_fee_rate(market)` reads each market's own rate. On that run the Ballon d'Or markets carried 0.03, and the NBA games and a Fed market 0.0, none of them the 0.07 crypto rate. Never hardcode it.

The fee functions are in `scanner.py` for your own checks:

```python
from scanner import calculate_fee, min_spread_for_breakeven
calculate_fee(500, 0.30, 0.07)            # 7.35, the chapter's 4.90% example
min_spread_for_breakeven(0.50, 0.07)      # 0.035 round trip as a taker; 0 as a maker
```
