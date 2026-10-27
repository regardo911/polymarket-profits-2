"""Optional: every default in one place. Appendix A, printed as the book prints it.

No bot imports this file. Each bot keeps these values as its own defaults
and its command-line flags (python <bot>.py --help). Keep it as a one-page
list of the numbers you can change, or import it and pass a value in:

    import config
    from arbitrage import run_arbitrage_scanner
    run_arbitrage_scanner(min_gap=config.ARBITRAGE_MIN_GAP)
"""
import os

# API Keys
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
NEWS_API_KEY = os.getenv("NEWS_API_KEY")
CLAUDE_MODEL = "claude-sonnet-5-5"

# Polymarket (public, read-only)
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DATA = "https://data-api.polymarket.com/v2"

# Strategy Parameters
ARBITRAGE_MIN_GAP = 0.005        # $ per pair, after fees
ARBITRAGE_MAX_COST = 50.0        # $ per pair trade
ARBITRAGE_SCAN_INTERVAL = 30     # seconds

RESOLUTION_MIN_EDGE = 0.10       # 10%
RESOLUTION_MAX_EVENT_AGE = 15    # seconds
RESOLUTION_POSITION_SIZE = 100.0

COPY_MAX_SLIPPAGE = 0.03         # 3%
COPY_MAX_POSITION_PCT = 0.05     # 5% of portfolio

CONTRARIAN_MIN_EDGE = 0.10       # 10%
CONTRARIAN_MAX_POSITION_PCT = 0.08
CONTRARIAN_AVOIDED_CATEGORIES = [
    "politics", "policy", "regulation",
    "merger", "acquisition"
]

AGENT_MAX_POSITIONS = 3
AGENT_MAX_POSITION_SIZE = 50.0   # $50
AGENT_MAX_DRAWDOWN = 15.0        # %

# Portfolio
STARTING_CAPITAL = 1000.0
CASH_RESERVE_PCT = 0.15
MAX_STRATEGY_ALLOCATION = 0.35
DAILY_DRAWDOWN_LIMIT = 0.05      # 5%
TOTAL_DRAWDOWN_LIMIT = 0.15      # 15%
