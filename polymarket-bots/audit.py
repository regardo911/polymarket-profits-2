"""Trade-log audit: EV per trade, win/loss ratio, Kelly and the chance it's luck. Chapter 10.

    python audit.py                          # trades.csv, the trainer's log
    python audit.py arbitrage_trades.csv     # any strategy's log
    python audit.py *_trades.csv trades.csv  # one verdict per log

Reads only the SELL rows (closed trades). Every number comes from your file.
Educational software, not financial advice.
"""
import argparse
import csv
import math


def streak_probability(wins, total, assumed_win_rate=0.50):
    """Probability of seeing this many wins by chance."""
    prob = 0
    for k in range(wins, total + 1):
        prob += math.comb(total, k) * (
            assumed_win_rate ** k
        ) * ((1 - assumed_win_rate) ** (total - k))
    return prob


def audit(filename="trades.csv"):
    """EV and luck check on the trainer's trade log (Ch3)."""
    pnls = []
    try:
        with open(filename) as f:
            for row in csv.reader(f):
                if row[1] == "SELL":              # closed trades
                    pnls.append(float(row[5].replace("$", "")))
    except FileNotFoundError:
        pass                                      # nothing logged yet
    if not pnls:
        print("VERDICT: too few trades to judge (0 closed). "
              "Let the trainer run.")
        return

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    n = len(pnls)
    win_rate = len(wins) / n
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    ev = win_rate * avg_win + (1 - win_rate) * avg_loss
    luck = streak_probability(len(wins), n)
    ratio = avg_win / abs(avg_loss) if avg_loss else 0.0
    kelly = win_rate - (1 - win_rate) / ratio if ratio else 0.0

    print(f"Closed trades: {n}")
    print(f"Win rate: {win_rate:.0%} | avg win ${avg_win:.2f} | "
          f"avg loss ${avg_loss:.2f}")
    print(f"Win/loss ratio: {ratio:.2f} (aim for 1.5+)")
    print(f"EV per trade: ${ev:+.2f}")
    print(f"Kelly from this log: {kelly:.1%} | "
          f"quarter-Kelly: {max(kelly, 0) / 4:.1%}")
    print(f"Chance of {len(wins)}+ wins in {n} by luck: {luck:.1%}")
    if n < 50:
        print("VERDICT: too few trades to judge (need 50+)")
    elif ev <= 0:
        print("VERDICT: negative EV. Fix sizing or stops first.")
    elif luck > 0.05:
        print("VERDICT: positive EV, but luck can't be ruled out yet.")
    else:
        print("VERDICT: positive EV that luck alone rarely produces.")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="EV, Kelly and luck check on a paper trade log.")
    parser.add_argument("logs", nargs="*", default=["trades.csv"],
                        help="one or more trade logs (default trades.csv)")
    args = parser.parse_args(argv)
    for i, filename in enumerate(args.logs):
        if len(args.logs) > 1:
            print(("\n" if i else "") + f"== {filename}")
        audit(filename)


if __name__ == "__main__":
    main()
