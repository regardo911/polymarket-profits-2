"""The paper-trading trainer: the test rig for every strategy. Chapter 3 (log calls from Chapter 10).

Watches live public order books, trades a fake bankroll, and appends every
paper fill to trades.csv for audit.py. No pUSD moves, no wallet, no key.

    python trainer.py
    python trainer.py --balance 500 --position-size 50 --markets 20

Paper trading only. Educational software, not financial advice.
"""
import argparse
import csv
import json
import time
from datetime import datetime

import requests

from scanner import GAMMA, get_markets, get_book, get_fee_rate, token_ids


class PaperPortfolio:
    def __init__(self, starting_balance=100.0, log_file=None):
        self.cash = starting_balance
        self.starting_balance = starting_balance
        self.positions = {}  # token_id -> {shares, avg_cost}
        self.trade_log = []
        self.peak_balance = starting_balance
        # Chapter 3 calls max drawdown "the largest percentage drop from any
        # peak", so the largest one seen is kept here; stats() also returns
        # the current drop as "drawdown".
        self.max_drawdown = 0.0
        # Optional CSV for audit.py: the strategy bots pass one in so each
        # strategy leaves its own log. The trainer calls log_trade() itself.
        self.log_file = log_file

    def _record(self, trade):
        self.trade_log.append(trade)
        if self.log_file:
            log_trade(trade, self.log_file)

    def buy(self, token_id, price, shares,
            market_name="", fee_rate=0.0):
        # Taker fee (Ch2 formula); fee_rate is 0.07 on crypto
        fee = shares * fee_rate * price * (1 - price)
        total_cost = price * shares + fee

        if total_cost > self.cash:
            shares = int(self.cash / (price + fee_rate * price * (1 - price)))
            fee = shares * fee_rate * price * (1 - price)
            total_cost = price * shares + fee
            if shares == 0:
                return False

        self.cash -= total_cost

        # avg_cost includes the buy fee, so sell P&L is net
        if token_id in self.positions:
            pos = self.positions[token_id]
            total_shares = pos["shares"] + shares
            pos["avg_cost"] = (
                (pos["avg_cost"] * pos["shares"] + total_cost)
                / total_shares
            )
            pos["shares"] = total_shares
        else:
            self.positions[token_id] = {
                "shares": shares,
                "avg_cost": total_cost / shares,
                "market_name": market_name
            }

        self._record({
            "action": "BUY",
            "token_id": token_id,
            "price": price,
            "shares": shares,
            "cost": total_cost,
            "fee": fee,
            "timestamp": time.time()
        })
        return True

    def sell(self, token_id, price, shares=None, fee_rate=0.0):
        if token_id not in self.positions:
            return False

        pos = self.positions[token_id]
        if shares is None:
            shares = pos["shares"]  # Sell all
        shares = min(shares, pos["shares"])

        # Takers pay the fee on sells too
        fee = shares * fee_rate * price * (1 - price)
        revenue = price * shares - fee
        cost_basis = pos["avg_cost"] * shares
        pnl = revenue - cost_basis

        self.cash += revenue
        pos["shares"] -= shares

        if pos["shares"] == 0:
            del self.positions[token_id]

        self._record({
            "action": "SELL",
            "token_id": token_id,
            "price": price,
            "shares": shares,
            "revenue": revenue,
            "fee": fee,
            "pnl": pnl,
            "timestamp": time.time()
        })
        return True

    def total_value(self, current_prices):
        position_value = sum(
            current_prices.get(tid, pos["avg_cost"]) * pos["shares"]
            for tid, pos in self.positions.items()
        )
        total = self.cash + position_value
        self.peak_balance = max(self.peak_balance, total)
        return total

    def stats(self, current_prices):
        sells = [t for t in self.trade_log if t["action"] == "SELL"]
        wins = [t for t in sells if t["pnl"] > 0]
        total = self.total_value(current_prices)
        drawdown = (
            (self.peak_balance - total) / self.peak_balance * 100
            if self.peak_balance > 0 else 0
        )
        self.max_drawdown = max(self.max_drawdown, drawdown)

        return {
            "total_value": total,
            "cash": self.cash,
            "return_pct": (total - self.starting_balance)
                / self.starting_balance * 100,
            "total_trades": len(sells),
            "win_rate": len(wins) / len(sells) * 100
                if sells else 0,
            "max_drawdown": self.max_drawdown,
            "drawdown": drawdown
        }


class SimpleStrategy:
    def __init__(self, buy_threshold=-0.03, sell_threshold=0.03):
        self.buy_threshold = buy_threshold
        self.sell_threshold = sell_threshold
        self.price_history = {}  # token_id -> [prices]
        self.window = 20  # Look at last 20 price points

    def update(self, token_id, price):
        if token_id not in self.price_history:
            self.price_history[token_id] = []
        self.price_history[token_id].append(price)

        # Keep only the last N prices
        if len(self.price_history[token_id]) > self.window:
            self.price_history[token_id] = (
                self.price_history[token_id][-self.window:]
            )

    def signal(self, token_id, current_price):
        history = self.price_history.get(token_id, [])
        if len(history) < 5:
            return "HOLD"  # Not enough data

        avg = sum(history) / len(history)
        change = (current_price - avg) / avg

        if change <= self.buy_threshold:
            return "BUY"
        elif change >= self.sell_threshold:
            return "SELL"
        return "HOLD"


def log_trade(trade, filename="trades.csv"):
    with open(filename, "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            datetime.now().isoformat(),
            trade["action"],
            trade["token_id"][:12],
            f"${trade.get('price', 0):.2f}",
            trade.get("shares", 0),
            f"${trade.get('pnl', 0):.2f}"
        ])


def settle_resolved_positions(portfolio):
    """Close paper positions whose market has resolved, at the payout.

    Not printed in the book. Arbitrage and resolution trades are held to
    resolution, so without this their slices never record a SELL and the
    Chapter 12 weekly report shows INSUFFICIENT DATA forever. A resolved
    market's outcomePrices reads ["1", "0"] or ["0", "1"]; each share is
    sold back to the paper portfolio at that payout with no fee, the way a
    live wallet would redeem it.
    """
    settled = []
    for token_id in list(portfolio.positions):
        try:
            # closed=true: Gamma leaves closed markets out of a token lookup
            # without it, and only a closed market can pay out
            r = requests.get(f"{GAMMA}/markets",
                             params={"clob_token_ids": token_id, "closed": "true"},
                             timeout=30)
            r.raise_for_status()
            found = r.json()
        except requests.RequestException:
            continue
        market = found[0] if found else {}
        if not (market.get("closed")
                and market.get("umaResolutionStatus") == "resolved"):
            continue
        ids = token_ids(market)
        prices = json.loads(market.get("outcomePrices") or "[]")
        if token_id not in ids or len(prices) != len(ids):
            continue
        payout = float(prices[ids.index(token_id)])
        shares = portfolio.positions[token_id]["shares"]
        if portfolio.sell(token_id, payout, fee_rate=0.0):
            pnl = portfolio.trade_log[-1]["pnl"]
            print(f"  SETTLED: {shares} shares of {token_id[:12]}... "
                  f"paid ${payout:.2f} each | P&L ${pnl:+.2f}")
            settled.append(portfolio.trade_log[-1])
    return settled


def run_trainer(starting_balance=100.0, position_size=10.0,
                watch_count=5, buy_threshold=-0.03, sell_threshold=0.03,
                interval=60, log_file="trades.csv"):
    # The book's run_trainer() takes no arguments; these keywords carry its
    # printed values as defaults so the command-line flags can reach them.
    portfolio = PaperPortfolio(starting_balance=starting_balance)
    strategy = SimpleStrategy(buy_threshold, sell_threshold)  # 3% thresholds

    # Pick 5 busy markets that are moving: the busiest 100,
    # biggest move in the last hour first, with a two-sided
    # book and a price between 10 and 90 cents. A market that
    # sat still all hour rarely crosses a 3% band.
    markets = get_markets(limit=100)
    markets.sort(key=lambda m: abs(
        float(m.get("oneHourPriceChange") or 0)), reverse=True)
    watchlist = []
    for market in markets:
        ids = token_ids(market)
        if not ids:
            continue
        bids, asks = get_book(ids[0])
        if not bids or not asks:
            continue
        if not 0.10 <= (bids[0][0] + asks[0][0]) / 2 <= 0.90:
            continue                     # all but decided
        watchlist.append({
            "token_id": ids[0],
            "question": market["question"][:50],
            "fee_rate": get_fee_rate(market)
        })
        if len(watchlist) >= watch_count:
            break

    print("=== Paper Trading Bot Started ===")
    print(f"Starting balance: ${portfolio.cash:.2f}")
    print(f"Monitoring {len(watchlist)} markets")
    print(f"Position size: ${position_size:.2f}")
    print(f"Trade log: {log_file}")
    print()

    cycle = 0
    while True:
        cycle += 1
        current_prices = {}
        timestamp = datetime.now().strftime("%H:%M:%S")

        for item in watchlist:
            tid = item["token_id"]
            try:
                bids, asks = get_book(tid)
                if not bids or not asks:
                    continue
                bid, ask = bids[0][0], asks[0][0]   # best prices
                mid = (bid + ask) / 2
                current_prices[tid] = mid

                strategy.update(tid, mid)
                sig = strategy.signal(tid, mid)

                if sig == "BUY" and tid not in portfolio.positions:
                    shares = int(position_size / ask)
                    if shares > 0 and portfolio.buy(
                        tid, ask, shares, item["question"],
                        fee_rate=item["fee_rate"]
                    ):
                        log_trade(portfolio.trade_log[-1], log_file)
                        print(
                            f"[{timestamp}] BUY {shares} "
                            f"@ ${ask:.2f} | "
                            f"{item['question']}"
                        )

                elif (sig == "SELL"
                      and tid in portfolio.positions):
                    if portfolio.sell(tid, bid,
                                      fee_rate=item["fee_rate"]):
                        log_trade(portfolio.trade_log[-1], log_file)
                        pnl = portfolio.trade_log[-1]["pnl"]
                        print(
                            f"[{timestamp}] SELL "
                            f"@ ${bid:.2f} | "
                            f"P&L: ${pnl:+.2f} | "
                            f"{item['question']}"
                        )

            except Exception as e:
                print(f"[{timestamp}] skipped {tid[:12]}: {e}")
                continue

        # status block every 10 cycles (10 minutes at the default interval)
        if cycle % 10 == 0:
            stats = portfolio.stats(current_prices)
            print(f"\n--- Cycle {cycle} Status ---")
            print(
                f"Portfolio: ${stats['total_value']:.2f} "
                f"({stats['return_pct']:+.1f}%)"
            )
            print(
                f"Cash: ${stats['cash']:.2f} | "
                f"Trades: {stats['total_trades']} | "
                f"Win rate: {stats['win_rate']:.0f}%"
            )
            print(
                f"Max drawdown: {stats['max_drawdown']:.1f}%"
            )
            print()

        time.sleep(interval)  # Check every minute


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Paper-trade live Polymarket books with the Chapter 3 test strategy.")
    parser.add_argument("--balance", type=float, default=100.0, help="paper bankroll in $ (default 100)")
    parser.add_argument("--position-size", type=float, default=10.0, help="$ per trade (default 10)")
    parser.add_argument("--markets", type=int, default=5, help="markets to watch (default 5)")
    parser.add_argument("--buy-threshold", type=float, default=-0.03,
                        help="buy when price is this far below its 20-point average (default -0.03)")
    parser.add_argument("--sell-threshold", type=float, default=0.03,
                        help="sell when price is this far above its average (default 0.03)")
    parser.add_argument("--interval", type=int, default=60, help="seconds between cycles (default 60)")
    parser.add_argument("--log", default="trades.csv", help="trade log for audit.py (default trades.csv)")
    args = parser.parse_args(argv)
    try:
        run_trainer(args.balance, args.position_size, args.markets,
                    args.buy_threshold, args.sell_threshold, args.interval, args.log)
    except KeyboardInterrupt:
        print(f"\nStopped. Your paper fills are in {args.log}: run python audit.py {args.log}")


if __name__ == "__main__":
    main()
