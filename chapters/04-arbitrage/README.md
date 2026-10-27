# Chapter 4: the arbitrage scanner, and the guarded live pair

`arbitrage.py` looks for markets tied by a hard rule: Up + Down in a 5-minute window, strikes on one ladder, dates in one deadline series. It prices each pair at the best asks and subtracts both taker fees. It paper-buys both legs only when the gap clears `min_gap` with at least 10 pairs at the top of the book.

```bash
cd polymarket-bots
python arbitrage.py
```

One real pass:

```
=== Arbitrage Scanner Started ===
Min gap after fees: $0.005 | max $50 per pair trade
[18:10:53] cycle 1: 244 pairs listed
  closest WINDOW   Bitcoin Up or Down | cost 1.010 + fees 0.0349 | gap -0.0449
  closest LADDER   Ethereum above ___ 2,700/2,800 | cost 0.999 + fees 0.0006 | gap +0.0004
  closest DEADLINE Will Predict.fun launch  pair 1 | cost 1.004 + fees 0.0006 | gap -0.0046
  total checked 171 | tradeable 0
```

That ladder pair cost less than $1.00 after fees, by $0.0004, which is under the $0.005 floor. A pass takes about two minutes.

**Success:** after about 15 minutes `total checked` passes 1,000 (the chapter's checkpoint) and each kind prints its closest gap. Zero tradeable is the normal result. If the scanner does take a pair you'll see `PAPER PAIR: N pairs | locked $X at resolution`, and both legs sit in the portfolio with the same share count.

## Your own settings

The three presets are assumptions to test on paper, not results:

| Preset | Command |
|---|---|
| Conservative | `python arbitrage.py --min-gap 0.01 --max-cost 25 --scan-interval 60` |
| Moderate (the book's defaults) | `python arbitrage.py` |
| Aggressive | `python arbitrage.py --min-gap 0.002 --max-cost 100 --scan-interval 15` |

Start conservative. Every paper fill goes to `arbitrage_trades.csv`. When a market resolves, the scanner closes the pair at the payout on paper ($1.00 a share for the winning leg, $0.00 for the other), so the log shows whether your locked gaps actually paid. That settlement step is this repo's addition; [BOOK-FIXES.md](../../BOOK-FIXES.md) says why. After 50 closed trades:

```bash
python audit.py arbitrage_trades.csv
```

A locked pair can't lose. Any losing row in that log is a bug or a one-legged fill, so find out which.

## The guarded live pair (`live_pair.py`)

This is the live setup the chapter points to: limit orders on both legs, a fill check, and the one-legged unwind. As shipped it prints the two orders it would send and stops.

```bash
python live_pair.py                  # the BTC window that's open now
python live_pair.py --asset eth
python live_pair.py --legs <YES token of the bigger event> <NO token of the smaller one>
```

```
Pair:
  leg 1  BUY 74 @ $0.340  10143561126112  Bitcoin Up or Down - October 26, 9:05PM-9:10PM ET
  leg 2  BUY 74 @ $0.670  12644356592986  Bitcoin Up or Down - October 26, 9:05PM-9:10PM ET
  per pair: cost 1.010 + fees 0.0312 | gap after fees -0.0412
  74 pairs, about $74.74 before fees
  tradeable: no (gap after fees -0.0412)
Geoblock: blocked=True country=US  -> the international live path refuses here; US readers use --platform us
DRY RUN: no order sent. Live mode is off; turning it on is an edit at the bottom of this file.
```

It needs your own account and credentials, and the SDK for where you live:

| You live | SDK | Environment variables |
|---|---|---|
| Outside the blocked and close-only countries | `polymarket-client` (`SecureClient`, Python 3.11+) | `POLYMARKET_PRIVATE_KEY`; `POLYMARKET_WALLET_ADDRESS` only for an older wallet (new accounts are Deposit Wallets) |
| In the United States | `polymarket-us` (Python 3.10+) | `POLYMARKET_KEY_ID`, `POLYMARKET_SECRET_KEY` |

```bash
python3 -m venv .venv-live && source .venv-live/bin/activate
pip install -r requirements-live.txt
```

Give it its own environment: `polymarket-client` 0.14.0 needs `websockets` below 16, and the book pins 17.3 for `stream.py`, so pip refuses to put both in one. `live_pair.py` only needs `requests` and the SDKs, which is all that file installs.

What you get back: two limit orders at the prices you chose, a fill check that polls each order until both legs fill or `--wait` seconds pass, and, if only one leg filled, a cancel of the other order and a sell of the unpaired shares at the best bid. The US path takes its two legs as `--us-leg <slug> long|short <YES price>`, since Polymarket US quotes every order at the YES price. It won't go live until you write your fee per pair into `US_FEE_PER_PAIR`.

To go live you edit the file: uncomment `set_live_mode(True, confirm="I_HAVE_REVIEWED_THIS")` at the bottom. There is no flag for it. Even then it refuses a blocked country on the international path, a pair that fails the Chapter 4 filter, and anything over $200. The file names every SDK call it makes and where each name came from; it has been checked to install and load, and it has never been run against a real account here.

The chapter's own list before any of that:

1. Run the paper scanner for at least 7 days and log every pair it would have traded.
2. Verify positive EV from the paper log, after fees.
3. Start with $100-200 of real capital, not your bankroll.
4. Watch every trade for the first 24 hours.
5. If more than 20% of pair trades fill on only one leg, your scanner is too slow or the markets are too thin.

Don't trust a fill check that waits for `FILLED`: polymarket.com has no such status (orders are `live`, `matched`, `delayed`, `unmatched`). And don't route around a geoblock with a VPN.
