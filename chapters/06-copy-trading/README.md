# Chapter 6: copy trading

Pick a wallet first, then point the bot at it. Both steps read Polymarket's public Data API, no key.

```bash
cd polymarket-bots
python copy_trader.py                      # this month's leaderboard
python copy_trader.py --period day         # who is trading today
python copy_trader.py 0x5924ca480d8b08cd5f3e5811fa378c4082475af6
```

The last one scores the $178K wallet from Chapter 1 with `analyze_wallet()`:

```
              trades: 19216
   return_per_dollar: 0.034046825777909254
  weekly_consistency: 16.666666666666664
        last_4_weeks: [0, 0, 0, 0]
             verdict: SKIP
              reason: 3.4% per dollar is under your taker fee
```

A famous wallet, and a copier paying taker fees would have lost money on it. Only a COPY verdict is worth the next step.

## Run the copier

The book prints this start script without a file name; here it is `copy_start.py`.

```bash
python copy_start.py --target-wallet 0x<the wallet you picked> --target-value 30000
```

`--target-value` is your estimate of the target's bankroll. The book uses 200,000; the public data doesn't give you the real number, so set your own. It decides every copy's size: at $500 against an estimated $30,000, a target's 1% trade becomes a $5 copy, and anything under $5 is skipped.

Forty seconds against the top wallet on the daily leaderboard (address cut):

```
Monitoring wallet: 0x(wallet)...
  TARGET #1: BUY 3164 Jazz @ $0.450 | seen 14s later | Grizzlies vs. Jazz
  SKIP #1: Trade too small at current scale
  TARGET #2: BUY 185 Hawks @ $0.460 | seen 11s later | Hawks vs. Rockets
  SKIP #2: Trade too small at current scale
```

You're through the chapter when 5 or more TARGET lines have printed with a size decision and a `seen Ns later` delay. Skips are fine; a bot that copied every one of these would pay the price move every time. Without a real `0x` + 40-hex wallet the script stops with `TARGET_WALLET is not a wallet address`.

Other knobs: `--balance` (500), `--max-slippage` (0.03), `--max-position-pct` (0.05), `--poll-seconds` (5). Copies go to `copy_trades.csv`.

Stop copying when the rolling EV falls, when the wallet changes what it trades, or when the delay cost eats more than half the average trade's profit.
