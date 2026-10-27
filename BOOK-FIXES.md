# Book fixes

The places found so far where the printed code doesn't do what its own chapter says, each with the output that shows it and what the files here do instead. Each one has a test in `tests/test_book_fixes.py`. The book's numbers, names and defaults are unchanged everywhere else.

## Chapter 3: max drawdown resets to zero when the portfolio recovers

Chapter 3 calls it "the largest percentage drop from any peak", but `stats()` returns the drop right now. Buy 100 shares at $0.50 on a $100 bankroll, let the price fall to $0.40, then come back:

```
drop to $0.40 : 10.0
back to $0.50 : 0.0
```

`PaperPortfolio` now keeps the largest drop as `max_drawdown` and also returns the current one as `drawdown`. The Claude agent's 15% stop reads `drawdown`, which is what its prompt says: "down more than 15% from peak".

## Chapter 8: category words match inside other words

`classify_market()` and `extract_primary_keyword()` test bare substrings, so "eth" is found in "Netherlands" and "sol" in "resolve":

```
'Will the Netherlands win the World Cup?' -> 'crypto_price'  kw='ethereum'
'Will the bill resolve the shutdown?'     -> 'crypto_price'  kw='solana'
'Will Sevilla win?'                       -> 'other'
```

The sentiment word lists have the same problem: "no" is found in "now" and "know", "lock" in "blockchain". Six neutral headlines scored 0.0, a 100% bearish crowd.

Short words now match only as whole words, longer ones at the start of a word. "ethereum" and "solana" join the coin list, since "eth" and "sol" used to catch them by accident. All the docstring examples still give the printed answers.

## Chapter 8: a 95%-bearish crowd triggers the same NO buy as a bullish one

`scan()` accepts `sentiment > 0.85 or sentiment < 0.15`, and `execute()` only ever buys NO. With a 95%-bearish crowd and a price above the base rate it returned `[('SELL_YES', 0.05)]`: buying NO alongside the bearish crowd, not against it. The chapter says the bearish case is "the reverse". The bot now fades only a crowd that is more than 85% bullish, the one case its code can trade.

## Chapter 8: one resolved position stops the contrarian bot

A resolved market has no order book. The CLOB answers `404 {"error":"No orderbook exists for the requested token id"}` and `get_book()` raises. `manage_exits()` has no handler, so the first position the bot holds to resolution ends the whole loop:

```
HTTPError: 404 Client Error: Not Found for url: https://clob.polymarket.com/book?token_id=...
```

`manage_exits()` now skips a token whose book can't be read, and the loop settles resolved positions first (see the Chapter 4 and Chapter 5 entry).

## Chapter 5: the redemption check never sees a market settle, or a NO position

`run_resolution_scanner()` reads the markets once at start, so a market that settles during the run still looks LIVE, and new hourly ladders never appear. Five passes of the loop:

```
get_markets called 1x, get_events called 1x
NO position in a SETTLED market -> queue: []
```

`markets_by_token` is keyed by the YES token only, so a NO position is never found. The scanner now re-reads the markets every pass, maps both tokens, and fetches the market behind each held token before the 5-minute redemption check.

## Chapter 4 and Chapter 5: paper positions never close, so the Monday report never fills

Arbitrage and resolution trades are held to resolution, but nothing in either file ever sells:

```
grep '.sell(' arbitrage.py resolution.py -> no matches
```

Their slices never write a SELL, so `weekly_edge_report()` prints INSUFFICIENT DATA for them forever and Chapter 4's check ("locked gaps that actually paid at resolution") has nothing to read. `settle_resolved_positions()` in `trainer.py` closes a held position once its market resolves: it reads Gamma's `outcomePrices` and sells each share back to the paper portfolio at $1.00 or $0.00 with no fee, the way a live wallet would redeem it. Arbitrage runs it every pass, resolution every 5 minutes, contrarian every scan. This is the one behavior the repo adds.

## Chapter 7: paper_trade says the reasoning was logged when nothing logs it

`paper_trade` returns `"reasoning_logged": True`, but the trade log has no reasoning field and no file is written:

```
trade_log keys: ['action', 'fee', 'pnl', 'price', 'revenue', 'shares', 'timestamp', 'token_id']
new files written: none
```

Every cycle now appends its tool calls, tool results, reasoning text and token counts to `agent_log.jsonl` (Appendix C, Walkthrough 5 describes exactly this log). `reasoning_logged` is true only after the reasoning line is written.

## Chapter 7: a SELL reports the shares asked for, not the shares sold

Holding 47 shares, a $25 sell at $0.50 asks for 50. The portfolio sells 47 and the tool tells the agent 50:

```
tool result: {'success': True, 'shares': 50, 'price': 0.5, 'reasoning_logged': True}
held 47, sold: 47
```

The tool now reports what the paper portfolio filled.

## Chapter 7: the cycle cost keeps Sonnet 5.5 prices after a model swap

Chapter 12 says to swap `model=` and compare the cost the agent prints. The price is written into the cost line, so a swapped model prints the old model's cost:

```
model sent: claude-haiku-5-5   Cycle cost: $0.0300
model sent: claude-sonnet-5-5  Cycle cost: $0.0300
```

The model and its per-million-token prices now sit together in `PRICES_PER_MILLION`. A model with no price there prints `Cycle cost: unknown, you must observe this` until you pass `--input-price` and `--output-price`.

## Chapter 10: audit.py can only read trades.csv

Chapter 12 asks for "audit.py on each strategy's log". Handed a 60-trade `arbitrage.csv`, the printed script ignores it and reads `trades.csv`:

```
$ python audit.py arbitrage.csv
VERDICT: too few trades to judge (0 closed). Let the trainer run.
```

`audit.py` now takes one or more log paths, and each bot writes its own log (`arbitrage_trades.csv`, `resolution_trades.csv` and so on; the trainer still writes `trades.csv`).

## Chapter 2: min_spread_for_breakeven() isn't in any file

It is printed next to `calculate_fee()` but outside the `scanner.py` listing, so nothing can import it:

```
ImportError: cannot import name 'min_spread_for_breakeven' from 'scanner'
```

It lives in `scanner.py` now, where Appendix A lists it.

## Chapter 2: stream.py has no way to run it

The run line is a comment, so `python stream.py 123456` exits 0 and prints nothing. It now takes the token id as an argument, and with no argument it streams the busiest market that has a two-sided book.
