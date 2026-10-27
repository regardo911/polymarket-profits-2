"""Portfolio manager: the five strategy bots on paper slices of one bankroll. Chapter 9 (+ the Chapter 12 Monday report).

Each strategy runs in its own thread on its own PaperPortfolio slice and logs
to <name>_trades.csv. Every hour: kill-switch alerts and the dashboard. Every
day: record_day(). Mondays: weekly_edge_report(). Every week: rebalance().

    python portfolio_manager.py
    python portfolio_manager.py --capital 2000 --target-wallet 0x<40 hex> --target-value 30000

The copy thread starts only with a real wallet; the Claude thread only with
ANTHROPIC_API_KEY (billed to you per cycle); the contrarian bot trades only
with NEWS_API_KEY. The others need nothing. The first dashboard prints after
an hour.

Paper trading only. Educational software, not financial advice.
"""
import argparse
import os
import re
import threading
import time
from datetime import datetime

import anthropic

from trainer import PaperPortfolio
from arbitrage import run_arbitrage_scanner
from resolution import run_resolution_scanner
from copy_trader import CopyTradingBot
from claude_agent import run_trading_agent
from contrarian import run_contrarian_bot
from scanner import get_markets
from utils import PlatformChangeMonitor, weekly_edge_report


class PortfolioManager:
    def __init__(self, total_capital, log_dir=None):
        self.total_capital = total_capital
        self.log_dir = log_dir          # set: each slice writes <name>_trades.csv
        self.peak_value = total_capital
        self.day, self.day_peak = None, total_capital
        self.strategies = {}
        self.allocations = {}
        self.combined_pnl = []
        self.reserve = total_capital    # cash no strategy holds

    def add_strategy(self, name, strategy, initial_pct):
        allocation = self.total_capital * initial_pct
        self.reserve -= allocation
        self.strategies[name] = {
            "strategy": strategy,
            "portfolio": PaperPortfolio(
                allocation,
                log_file=(os.path.join(self.log_dir, f"{name}_trades.csv")
                          if self.log_dir else None)),
            "trades": [],
            "daily_returns": [],
            "last_value": allocation
        }
        self.allocations[name] = initial_pct

    def record_day(self):
        """Run once a day: store each strategy's daily return."""
        today = datetime.now().strftime("%Y-%m-%d")
        for data in self.strategies.values():
            value = data["portfolio"].total_value({})
            prev = data["last_value"]
            data["daily_returns"].append(
                {"date": today, "return": (value - prev) / prev}
            )
            data["last_value"] = value

    MIN_PCT, MAX_PCT, RESERVE_PCT = 0.05, 0.35, 0.15

    def rebalance(self):
        """Move capital toward the best risk-adjusted returns.

        Each strategy gets 5% to 35% of the total, and 15%
        stays in the cash reserve. Paper cash moves between
        the slices.
        """
        sharpe_ratios = {}

        for name, data in self.strategies.items():
            returns = [r["return"] for r in data["daily_returns"]]
            if len(returns) < 7:  # Need at least a week
                sharpe_ratios[name] = 1.0  # Default
                continue

            avg_return = sum(returns) / len(returns)
            std_return = (
                sum((r - avg_return) ** 2 for r in returns)
                / len(returns)
            ) ** 0.5

            if std_return == 0:
                sharpe_ratios[name] = 0
            else:
                sharpe_ratios[name] = avg_return / std_return

        # Split 85% by Sharpe. Pin any share above 35% (then
        # any below 5%) to its limit and split the rest again.
        weights = {n: max(s, 0.1) for n, s in sharpe_ratios.items()}
        pinned = {}
        while len(pinned) < len(weights):
            free = [n for n in weights if n not in pinned]
            left = 1 - self.RESERVE_PCT - sum(pinned.values())
            free_weight = sum(weights[n] for n in free)
            shares = {n: left * weights[n] / free_weight for n in free}
            high = {n: self.MAX_PCT
                    for n, p in shares.items() if p > self.MAX_PCT}
            low = {n: self.MIN_PCT
                   for n, p in shares.items() if p < self.MIN_PCT}
            if not (high or low):
                break
            pinned.update(high or low)    # caps first, then floors
        self.allocations = {**shares, **pinned}

        # Move paper cash so each slice matches its new share.
        # Slices above target give cash first (open positions
        # stay put), then slices below target are topped up
        # from the reserve, never taking it under 15%.
        total = self.reserve + sum(
            d["portfolio"].total_value({})
            for d in self.strategies.values())
        gaps = {n: total * self.allocations[n]
                - d["portfolio"].total_value({})
                for n, d in self.strategies.items()}
        for name in sorted(gaps, key=gaps.get):
            data = self.strategies[name]
            if gaps[name] < 0:
                amount = -min(-gaps[name], data["portfolio"].cash)
            else:
                spare = self.reserve - total * self.RESERVE_PCT
                amount = min(gaps[name], max(spare, 0))
            self.move_cash(data, amount)

    def move_cash(self, data, amount):
        """Add paper cash to a slice (take it, if negative)."""
        pf = data["portfolio"]
        pf.cash += amount
        pf.starting_balance += amount   # a transfer isn't profit
        pf.peak_balance += amount
        data["last_value"] += amount
        self.reserve -= amount


def pearson_correlation(x, y):
    """Pearson correlation coefficient between two lists.

    Returns a value between -1.0 (perfect inverse) and
    1.0 (perfect positive). 0.0 means no linear relationship.
    """
    n = len(x)
    if n < 2:
        return 0.0
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    cov = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n))
    std_x = (sum((xi - mean_x) ** 2 for xi in x)) ** 0.5
    std_y = (sum((yi - mean_y) ** 2 for yi in y)) ** 0.5
    if std_x == 0 or std_y == 0:
        return 0.0
    return cov / (std_x * std_y)


def align_returns(r1, r2):
    """Align two return series by date index.

    Each series is a list of {"date": "YYYY-MM-DD", "return": 0.02},
    the records record_day() writes.
    Returns list of (r1_return, r2_return) tuples for matching dates.
    """
    r2_by_date = {r["date"]: r["return"] for r in r2}
    paired = []
    for entry in r1:
        if entry["date"] in r2_by_date:
            paired.append((entry["return"], r2_by_date[entry["date"]]))
    return paired


def calculate_correlations(strategy_returns):
    """Calculate pairwise correlation between strategies."""
    names = list(strategy_returns.keys())
    n = len(names)
    correlations = {}

    for i in range(n):
        for j in range(i + 1, n):
            r1 = strategy_returns[names[i]]
            r2 = strategy_returns[names[j]]

            paired = align_returns(r1, r2)
            if len(paired) < 14:
                correlations[f"{names[i]} / {names[j]}"] = None
                continue

            corr = pearson_correlation(
                [p[0] for p in paired],
                [p[1] for p in paired]
            )
            correlations[f"{names[i]} / {names[j]}"] = round(corr, 2)

    return correlations


def check_portfolio_health(manager):
    """Portfolio-level risk checks."""
    total_value = manager.reserve + sum(
        d["portfolio"].total_value({})
        for d in manager.strategies.values()
    )
    manager.peak_value = max(manager.peak_value, total_value)
    manager.combined_pnl.append(total_value)

    # The daily high starts over on each new calendar day
    today = datetime.now().strftime("%Y-%m-%d")
    if manager.day != today:
        manager.day, manager.day_peak = today, total_value
    manager.day_peak = max(manager.day_peak, total_value)

    daily_drop = ((manager.day_peak - total_value)
                  / manager.day_peak * 100)
    drawdown = ((manager.peak_value - total_value)
                / manager.peak_value * 100)

    alerts = []

    if daily_drop > 5:
        alerts.append(
            f"DAILY DRAWDOWN: {daily_drop:.1f}% — PAUSE ALL"
        )
    if drawdown > 15:
        alerts.append(
            f"TOTAL DRAWDOWN: {drawdown:.1f}% — HALT ALL"
        )

    # no slice may hold more than 35% of the combined value
    for name, data in manager.strategies.items():
        strat_value = data["portfolio"].total_value({})
        concentration = strat_value / total_value * 100
        if concentration > 35:
            alerts.append(
                f"{name} concentration: "
                f"{concentration:.0f}% — REBALANCE"
            )

    return alerts


def portfolio_dashboard(manager):
    """Print a combined dashboard for all strategies."""
    print("\n" + "=" * 60)
    print("PORTFOLIO DASHBOARD")
    print("=" * 60)

    total_value = manager.reserve     # cash no strategy holds
    total_trades = 0

    for name, data in manager.strategies.items():
        pf = data["portfolio"]
        stats = pf.stats({})
        total_value += stats["total_value"]
        total_trades += stats["total_trades"]
        alloc = manager.allocations.get(name, 0) * 100

        print(
            f"\n{name.upper()} "
            f"({alloc:.0f}% allocated):"
        )
        print(
            f"  Value: ${stats['total_value']:.2f} | "
            f"Return: {stats['return_pct']:+.1f}% | "
            f"Trades: {stats['total_trades']} | "
            f"Win rate: {stats['win_rate']:.0f}%"
        )
        print(
            f"  Max drawdown: {stats['max_drawdown']:.1f}%"
        )

    start = manager.total_capital      # reserve + slices at start
    overall_return = (total_value - start) / start * 100
    print(f"\n{'─' * 60}")
    print(f"Cash reserve: ${manager.reserve:.2f}")
    print(f"COMBINED, reserve included: ${total_value:.2f} "
          f"({overall_return:+.1f}%)")
    print(f"Total trades: {total_trades}")

    # Show correlations if enough data
    returns = {}
    for name, data in manager.strategies.items():
        if data["daily_returns"]:
            returns[name] = data["daily_returns"]

    if len(returns) >= 2:
        corrs = calculate_correlations(returns)
        print("\nCorrelations:")
        for pair, corr in corrs.items():
            if corr is not None:
                status = (
                    "OK" if abs(corr) < 0.3
                    else "WATCH" if abs(corr) < 0.6
                    else "HIGH"
                )
                print(f"  {pair}: {corr:.2f} [{status}]")


NEWS_KEY = os.environ.get("NEWS_API_KEY")
TARGET_WALLET = os.environ.get("TARGET_WALLET", "0xTARGET_WALLET")   # the wallet you picked in Ch6
TARGET_VALUE = 200_000   # your estimate of that wallet's bankroll


def agent_loop(portfolio):
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("claude: ANTHROPIC_API_KEY not set, agent not started")
        return
    while True:
        try:
            run_trading_agent(portfolio)  # one cycle
        except anthropic.AuthenticationError:
            print("claude: API key rejected, agent stopped")
            return
        except Exception as e:            # network, rate limit
            print(f"claude: cycle failed ({e}), next try in 15 min")
        time.sleep(900)                   # every 15 minutes


def copy_loop(portfolio):
    if not re.fullmatch(r"0x[0-9a-fA-F]{40}", TARGET_WALLET):
        print("copy: TARGET_WALLET is not a wallet address, "
              "copy bot not started")
        return
    CopyTradingBot(portfolio, TARGET_WALLET,
                   target_portfolio_value=TARGET_VALUE).run()


def main(argv=None):
    global TARGET_WALLET, TARGET_VALUE
    parser = argparse.ArgumentParser(description="Run all five strategy bots on paper.")
    parser.add_argument("--capital", type=float, default=1000.0, help="total paper capital (default 1000)")
    parser.add_argument("--target-wallet", default=TARGET_WALLET,
                        help="wallet for the copy thread (or set TARGET_WALLET)")
    parser.add_argument("--target-value", type=float, default=TARGET_VALUE,
                        help="your estimate of the target's bankroll (default 200000)")
    args = parser.parse_args(argv)
    TARGET_WALLET, TARGET_VALUE = args.target_wallet, args.target_value

    manager = PortfolioManager(total_capital=args.capital, log_dir=".")
    manager.add_strategy("arbitrage", lambda pf:
                         run_arbitrage_scanner(portfolio=pf), 0.25)
    manager.add_strategy("resolution", lambda pf:
                         run_resolution_scanner(portfolio=pf), 0.20)
    manager.add_strategy("copy", copy_loop, 0.15)
    manager.add_strategy("claude", agent_loop, 0.10)
    manager.add_strategy("contrarian", lambda pf:
                         run_contrarian_bot(pf, NEWS_KEY), 0.15)
    # the last 15% stays in the cash reserve

    for name, data in manager.strategies.items():
        threading.Thread(target=data["strategy"],
                         args=(data["portfolio"],),
                         name=name, daemon=True).start()

    monitor = PlatformChangeMonitor()
    monitor.check_for_changes(get_markets())   # first snapshot

    hours = 0
    while True:
        time.sleep(3600)
        hours += 1
        for alert in check_portfolio_health(manager):
            print(alert)
        portfolio_dashboard(manager)
        if hours % 24 == 0:
            manager.record_day()
            if datetime.now().weekday() == 0:      # Monday
                weekly_edge_report(manager, monitor, get_markets())
        if hours % (24 * 7) == 0:
            manager.rebalance()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped. Each strategy's paper fills are in <name>_trades.csv")
