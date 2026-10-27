"""Copy trading on Polymarket's public Data API: score a wallet, watch it, paper-copy it. Chapter 6.

    python copy_trader.py                 # this month's leaderboard: wallets to look at
    python copy_trader.py 0x<40 hex>      # analyze_wallet(): COPY / SKIP / RISKY / TOO SLOW

To run the bot itself on a wallet, use copy_start.py.

Paper trading only. Educational software, not financial advice.
"""
import argparse
import time
import requests
from scanner import GAMMA, get_book, get_fee_rate

DATA = "https://data-api.polymarket.com/v2"


def data_api(path, **params):
    """GET a Data API v2 endpoint. Results sit under "data"."""
    r = requests.get(f"{DATA}/{path}", params=params, timeout=30)
    r.raise_for_status()
    return r.json()["data"]


def leaderboard(period="month", limit=20):
    """Top wallets: rank, user_id (the wallet), pnl, volume."""
    return data_api("leaderboard", time_period=period, limit=limit)


def analyze_wallet(wallet, min_trades=50, fee_per_dollar=0.035):
    """Score a wallet from its public stats and daily P&L."""
    stats = data_api("user-stats", user=wallet)
    points = data_api("user-pnl", user=wallet, interval="all",
                      fidelity="1d")["points"]
    trades = stats["trades"]
    if trades < min_trades or len(points) < 14:
        return {"verdict": "INSUFFICIENT DATA",
                "reason": f"{trades} trades, {len(points)} days"}

    realized = stats["all_time_pnl"]["realized_pnl"]
    volume = stats["all_time_pnl"]["volume_usdc"] or 1
    days = len(points)

    # realized_pnl in the daily series is a running total:
    # take 7-day differences to get each week's result
    weeks = [points[i]["realized_pnl"] - points[i - 7]["realized_pnl"]
             for i in range(7, len(points), 7)]
    last4 = weeks[-4:]
    consistency = sum(1 for w in weeks if w > 0) / len(weeks) * 100

    result = {
        "wallet": wallet[:12] + "...",
        "realized_pnl": realized,
        "trades": trades,
        "pnl_per_trade": realized / trades,
        "return_per_dollar": realized / volume,
        "weekly_consistency": consistency,
        "last_4_weeks": [round(w) for w in last4],
        "trades_per_day": trades / days,
    }

    if result["return_per_dollar"] <= fee_per_dollar:
        # your copies are taker orders: 0.07 x (1 - 0.50) = 3.5%
        # of every dollar on a crypto market at 50 cents
        result["verdict"] = "SKIP"
        result["reason"] = (f"{result['return_per_dollar']*100:.1f}% "
                            f"per dollar is under your taker fee")
    elif sum(last4) <= 0:
        result["verdict"], result["reason"] = "SKIP", "Flat or losing for 4 weeks"
    elif consistency < 60:
        result["verdict"] = "RISKY"
        result["reason"] = f"Profitable in {consistency:.0f}% of weeks"
    elif result["trades_per_day"] < 1:
        result["verdict"] = "TOO SLOW"
        result["reason"] = f"{result['trades_per_day']:.1f} trades/day"
    else:
        result["verdict"] = "COPY"
        result["reason"] = (f"{result['return_per_dollar']*100:.1f}% "
                            f"per dollar traded, {consistency:.0f}% "
                            f"of weeks green")
    return result


class WalletMonitor:
    """Poll a wallet's public activity and report new trades."""

    def __init__(self, target_wallet, poll_seconds=5):
        self.wallet = target_wallet.lower()
        self.poll_seconds = poll_seconds
        self.seen = set()
        self.callbacks = []

    def on_trade(self, callback):
        self.callbacks.append(callback)

    def poll(self):
        rows = data_api("activity", user=self.wallet, limit=50)
        new = [r for r in rows
               if r["type"] == "TRADE"
               and r["transaction_hash"] + r["token_id"] not in self.seen]
        for r in rows:
            self.seen.add(r["transaction_hash"] + r["token_id"])
        return sorted(new, key=lambda r: r["timestamp"])

    def start(self):
        self.poll()                       # history: mark as seen
        print(f"Monitoring wallet: {self.wallet[:12]}...")
        while True:
            time.sleep(self.poll_seconds)
            try:
                rows = self.poll()
            except requests.RequestException as e:
                print(f"  poll failed: {e}")
                continue
            for r in rows:
                trade = {
                    "token_id": r["token_id"],
                    "side": r["side"],               # BUY / SELL
                    "price": float(r["price"]),
                    "shares": float(r["size"]),
                    "amount_usd": float(r["usdc_size"]),
                    "market_name": r["title"],
                    "outcome": r["outcome"],
                    "delay_s": time.time() - r["timestamp"],
                }
                for cb in self.callbacks:
                    cb(trade)


def calculate_copy_size(
    target_trade,
    target_portfolio_value,
    my_portfolio_value,
    max_position_pct=0.05
):
    """Scale a copy trade to match portfolio allocation."""
    target_amount = target_trade["amount_usd"]
    target_pct = target_amount / target_portfolio_value

    # Match the percentage allocation
    my_amount = target_pct * my_portfolio_value

    # Cap at maximum position size
    max_amount = max_position_pct * my_portfolio_value
    my_amount = min(my_amount, max_amount)

    # Floor at minimum viable trade ($5)
    if my_amount < 5.0:
        return 0, "Trade too small at current scale"

    shares = int(my_amount / target_trade["price"])

    return shares, (
        f"Target: {target_pct*100:.1f}% of portfolio, "
        f"Copy: ${my_amount:.2f} ({shares} shares)"
    )


def check_copy_viability(
    target_price,
    current_price,
    max_slippage_pct=0.03
):
    """Reject copy if price has moved too much since target's trade."""
    slippage = abs(current_price - target_price) / target_price

    if slippage > max_slippage_pct:
        return False, (
            f"Price moved {slippage*100:.1f}% since target trade "
            f"(max {max_slippage_pct*100:.0f}%)"
        )
    return True, f"Slippage: {slippage*100:.1f}%"


def market_for_token(token_id):
    """Gamma market for one outcome token (for its fee rate)."""
    r = requests.get(f"{GAMMA}/markets",
                     params={"clob_token_ids": token_id}, timeout=30)
    r.raise_for_status()
    found = r.json()
    return found[0] if found else {}


class CopyTradingBot:
    def __init__(self, portfolio, target_wallet,
                 target_portfolio_value, poll_seconds=5,
                 max_slippage_pct=0.03, max_position_pct=0.05):
        self.portfolio = portfolio
        self.target_value = target_portfolio_value
        self.monitor = WalletMonitor(target_wallet, poll_seconds)
        self.trade_count = 0
        # the two limits config.py lists; defaults are the printed ones
        self.max_slippage_pct = max_slippage_pct
        self.max_position_pct = max_position_pct

    def on_target_trade(self, trade):
        """Handle a detected trade from the target wallet."""
        self.trade_count += 1
        n = self.trade_count
        print(f"  TARGET #{n}: {trade['side']} {trade['shares']:.0f} "
              f"{trade['outcome']} @ ${trade['price']:.3f} | "
              f"seen {trade['delay_s']:.0f}s later | "
              f"{trade['market_name'][:40]}")

        # Current best price on the side you would take
        try:
            bids, asks = get_book(trade["token_id"])
        except requests.RequestException:
            print(f"  SKIP #{n}: can't read the book")
            return
        side = asks if trade["side"] == "BUY" else bids
        if not side:
            print(f"  SKIP #{n}: empty book")
            return
        current_price = side[0][0]        # best ask / best bid

        viable, reason = check_copy_viability(
            trade["price"], current_price, self.max_slippage_pct
        )
        if not viable:
            print(f"  SKIP #{n}: {reason}")
            return

        my_value = self.portfolio.total_value({})
        shares, sizing_info = calculate_copy_size(
            trade, self.target_value, my_value, self.max_position_pct
        )
        if shares == 0:
            print(f"  SKIP #{n}: {sizing_info}")
            return

        fee_rate = get_fee_rate(market_for_token(trade["token_id"]))
        if trade["side"] == "BUY":
            ok = self.portfolio.buy(
                trade["token_id"], current_price, shares,
                market_name=f"COPY: {trade['market_name'][:30]}",
                fee_rate=fee_rate)
        else:
            ok = self.portfolio.sell(
                trade["token_id"], current_price, shares,
                fee_rate=fee_rate)
        if ok:
            print(f"  COPY #{n}: {trade['side']} {shares} "
                  f"@ ${current_price:.3f} | {sizing_info}")

    def run(self):
        self.monitor.on_trade(self.on_target_trade)
        self.monitor.start()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Find and score a wallet to copy.")
    parser.add_argument("wallet", nargs="?", help="wallet to score with analyze_wallet()")
    parser.add_argument("--period", default="month", choices=["day", "week", "month", "all"],
                        help="leaderboard window when no wallet is given (default month)")
    parser.add_argument("--limit", type=int, default=20, help="leaderboard rows (default 20)")
    parser.add_argument("--min-trades", type=int, default=50, help="analyze_wallet floor (default 50)")
    parser.add_argument("--fee-per-dollar", type=float, default=0.035,
                        help="your taker cost per $ traded (default 0.035: rate 0.07 at 50 cents)")
    args = parser.parse_args(argv)

    if args.wallet:
        for key, value in analyze_wallet(args.wallet, args.min_trades,
                                         args.fee_per_dollar).items():
            print(f"{key:>20}: {value}")
        return
    print(f"Leaderboard, {args.period}: score one with python copy_trader.py <wallet>")
    for row in leaderboard(args.period, args.limit):
        print(f"  #{row.get('rank')}  {row.get('user_id')}  "
              f"pnl ${float(row.get('pnl') or 0):>12,.0f}  "
              f"volume ${float(row.get('volume') or 0):>14,.0f}  "
              f"{row.get('user_name') or ''}")


if __name__ == "__main__":
    main()
