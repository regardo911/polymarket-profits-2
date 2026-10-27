"""Sizing (Chapter 10) and edge-decay tools (Chapter 12). A library: the bots and the
portfolio manager import it, nothing here runs on its own.

    from utils import kelly_size
    kelly_size(1000, win_prob=0.60, entry_price=0.50, fee_rate=0.07)   # quarter-Kelly $ size

Educational software, not financial advice.
"""
import time

from scanner import token_ids


def check_stop_loss(position, current_price, stop_pct=0.15):
    """Trigger stop-loss if price drops 15% from entry."""
    entry = position["avg_cost"]
    loss_pct = (entry - current_price) / entry

    if loss_pct >= stop_pct:
        return True, (
            "Stop-loss triggered: "
            f"entry ${entry:.2f}, "
            f"current ${current_price:.2f}, "
            f"loss {loss_pct*100:.1f}%"
        )
    return False, None


def fixed_fraction_size(bankroll, fraction=0.02):
    """Bet 2% of bankroll per trade."""
    return bankroll * fraction


def kelly_size(bankroll, win_prob, entry_price,
               fee_rate=0.07, slippage_estimate=0.005,
               payout=1.0, fraction=0.25):
    """Quarter-Kelly position sizing with fee and slippage drag.

    fee_rate          The market's taker rate from get_fee_rate()
                      (Ch2). Default is crypto, 0.07.
    slippage_estimate Per-share slippage in dollars, from your own
                      fills (paper or live).
    fraction          0.25 = quarter-Kelly, 0.50 = half-Kelly, 1.0 = full
                      Kelly. Quarter is the conservative default; any
                      bet above twice the true Kelly fraction loses
                      money over time, and a few points of probability
                      error can push full Kelly past that line.
    """
    per_share_fee = fee_rate * entry_price * (1 - entry_price)
    net_payout = (
        payout - entry_price - per_share_fee - slippage_estimate
    ) / entry_price

    if net_payout <= 0:
        return 0  # Costs eat the entire payout

    kelly = (win_prob * net_payout - (1 - win_prob)) / net_payout
    if kelly <= 0:
        return 0  # Negative EV after costs, don't trade

    return bankroll * kelly * fraction


def edge_based_size(bankroll, edge_pct, max_pct=0.05):
    """Size position based on edge magnitude."""
    # Bigger edge = bigger position (up to cap)
    size_pct = min(edge_pct * 2, max_pct)
    return bankroll * size_pct


def detect_edge_decay(trade_log, window=30):
    """Check if average profit per trade is declining."""
    if len(trade_log) < window * 2:
        return None  # Not enough data

    recent = trade_log[-window:]
    previous = trade_log[-window*2:-window]

    recent_avg = sum(
        t["pnl"] for t in recent
    ) / len(recent)
    previous_avg = sum(
        t["pnl"] for t in previous
    ) / len(previous)

    decay_pct = (
        (previous_avg - recent_avg) / abs(previous_avg) * 100
        if previous_avg != 0 else 0
    )

    return {
        "previous_avg_pnl": previous_avg,
        "recent_avg_pnl": recent_avg,
        "decay_pct": decay_pct,
        "alert": decay_pct > 30  # 30%+ decay is a warning
    }


class PlatformChangeMonitor:
    """Watch Gamma market metadata for changes that move your
    edge at once: fees switched on, a new fee rate, a new tick
    size, a neg-risk flag flip."""

    def __init__(self):
        self.snapshots = {}  # token_id -> {field: last value}

    def fields(self, market):
        sched = market.get("feeSchedule") or {}
        return {
            "feesEnabled": market.get("feesEnabled"),
            "feeSchedule.rate": sched.get("rate"),
            "negRisk": market.get("negRisk"),
            "orderPriceMinTickSize": market.get("orderPriceMinTickSize"),
        }

    def check_for_changes(self, markets):
        """Compare each market (Gamma data) to its last snapshot."""
        changes = []
        for market in markets:
            ids = token_ids(market)
            if not ids:
                continue
            current = self.fields(market)
            previous = self.snapshots.get(ids[0])
            self.snapshots[ids[0]] = current
            if previous is None:
                continue          # first sighting: just remember it
            for field, value in current.items():
                if previous[field] != value:
                    changes.append({
                        "token_id": ids[0],
                        "field": field,
                        "previous": previous[field],
                        "current": value,
                        "question": market.get("question", "")[:60],
                        "timestamp": time.time()
                    })
        return changes


def calculate_sharpe(daily_returns):
    """Annualized Sharpe ratio from daily return percentages.

    Sharpe = (avg daily return / std of daily returns) * sqrt(365)
    > 2.0 = excellent, 1.0-2.0 = good, 0.5-1.0 = mediocre, < 0.5 = poor.
    """
    if len(daily_returns) < 7:
        return 0.0
    avg = sum(daily_returns) / len(daily_returns)
    variance = sum((r - avg) ** 2 for r in daily_returns) / len(daily_returns)
    std = variance ** 0.5
    if std == 0:
        return 0.0
    return (avg / std) * (365 ** 0.5)


def weekly_edge_report(manager, platform_monitor, markets):
    """Weekly edge health report for every strategy."""
    print("\n" + "=" * 60)
    print("WEEKLY EDGE HEALTH REPORT")
    print("=" * 60)

    for name, data in manager.strategies.items():
        closed = [t for t in data["portfolio"].trade_log
                  if t["action"] == "SELL"]
        decay = detect_edge_decay(closed)
        if decay is None:
            print(f"\n{name}: INSUFFICIENT DATA "
                  f"({len(closed)} closed trades, need 60)")
            continue

        returns = [r["return"] for r in data["daily_returns"]]
        sharpe_7d = calculate_sharpe(returns[-7:])
        sharpe_30d = calculate_sharpe(returns[-30:])

        print(f"\n{name.upper()}:")
        print("  Avg P&L (last 30 trades): "
              f"${decay['recent_avg_pnl']:.2f}")
        print("  Avg P&L (prev 30 trades): "
              f"${decay['previous_avg_pnl']:.2f}")
        print("  Edge decay: "
              f"{decay['decay_pct']:.0f}%"
              f"{'  *** WARNING ***' if decay['alert'] else ''}")
        print(f"  Sharpe (7d): {sharpe_7d:.2f} | "
              f"(30d): {sharpe_30d:.2f}")

        if decay['alert']:
            print("  ACTION: Reduce allocation. "
                  "Investigate cause.")
        elif sharpe_7d < 0.5:
            print("  ACTION: Monitor closely. "
                  "Sharpe below threshold.")
        else:
            print("  STATUS: Healthy.")

    platform_changes = platform_monitor.check_for_changes(markets)
    if platform_changes:
        print(f"\n--- Platform changes this week ({len(platform_changes)}) ---")
        for change in platform_changes:
            print(
                f"  {change['field']}: "
                f"{change['previous']} -> {change['current']} | "
                f"{change['question']}"
            )
        print("  ACTION: Re-run edge calculations on affected markets.")
    else:
        print("\nPlatform changes this week: none")
