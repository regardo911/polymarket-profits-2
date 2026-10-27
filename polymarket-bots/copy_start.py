"""The short start script from Chapter 6 (the book never names the file): paper-copy one wallet.

    python copy_start.py --target-wallet 0x<40 hex>
    python copy_start.py --target-wallet 0x... --target-value 30000 --balance 500

--target-value is your estimate of the target's bankroll. The book prints
200_000; nothing in the public data gives you the real figure, so set your own
(Appendix C, Walkthrough 4 uses an estimated $30,000). Pick a wallet that has
traded in the last few minutes or the monitor has nothing to show you.

Paper trading only. Educational software, not financial advice.
"""
import argparse
import os
import re
from trainer import PaperPortfolio
from copy_trader import CopyTradingBot

TARGET_WALLET = "0xTARGET_WALLET"   # paste the wallet you picked


def main(argv=None):
    parser = argparse.ArgumentParser(description="Paper-copy a Polymarket wallet.")
    parser.add_argument("--target-wallet", default=os.environ.get("TARGET_WALLET", TARGET_WALLET),
                        help="0x + 40 hex characters (or set TARGET_WALLET)")
    parser.add_argument("--target-value", type=float, default=200_000,
                        help="your estimate of the target's bankroll in $ (default 200000)")
    parser.add_argument("--balance", type=float, default=500.0, help="your paper bankroll (default 500)")
    parser.add_argument("--poll-seconds", type=int, default=5, help="seconds between polls (default 5)")
    parser.add_argument("--max-slippage", type=float, default=0.03,
                        help="skip a copy once price moved this far (default 0.03)")
    parser.add_argument("--max-position-pct", type=float, default=0.05,
                        help="cap per copy as a share of your bankroll (default 0.05)")
    parser.add_argument("--log", default="copy_trades.csv",
                        help="trade log for audit.py (default copy_trades.csv)")
    args = parser.parse_args(argv)

    if not re.fullmatch(r"0x[0-9a-fA-F]{40}", args.target_wallet):
        raise SystemExit("TARGET_WALLET is not a wallet address")

    bot = CopyTradingBot(PaperPortfolio(args.balance, log_file=args.log), args.target_wallet,
                         target_portfolio_value=args.target_value,
                         poll_seconds=args.poll_seconds,
                         max_slippage_pct=args.max_slippage,
                         max_position_pct=args.max_position_pct)
    try:
        bot.run()
    except KeyboardInterrupt:
        print(f"\nStopped after {bot.trade_count} target trades. Paper fills are in {args.log}")


if __name__ == "__main__":
    main()
