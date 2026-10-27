"""Contrarian bot: fair value, engagement-weighted sentiment, category filter, inverse positions. Chapter 8.

Trades only when the price sits at least 10% above its category's base rate
AND the crowd is more than 85% bullish, outside politics, policy, regulation
and M&A, and after the last big move has settled for 10 minutes. Sentiment
comes from NewsAPI headlines when NEWS_API_KEY is set; without it every score
is 0.50 and the bot only scans.

    python contrarian.py
    python contrarian.py --min-edge 0.08 --scan-interval 120

Paper trading only. Educational software, not financial advice.
"""
import argparse
import json
import os
import re
import time
from collections import defaultdict

import requests

from scanner import GAMMA, get_book, get_fee_rate, token_ids
from trainer import PaperPortfolio, settle_resolved_positions


def _mentions(text, term):
    """Whole-word match for short or space-ended terms ("eth", "fed "),
    word-start match for longer ones ("crypto" also finds "cryptocurrency").
    The book's bare substring test put "the Netherlands" in crypto and
    read "now" as a bearish "no"."""
    word = re.escape(term.strip())
    if len(term.strip()) <= 3 or term.endswith(" "):
        return re.search(rf"(?<![a-z0-9]){word}(?![a-z0-9])", text) is not None
    return re.search(rf"(?<![a-z0-9]){word}", text) is not None


def _mentions_phrase(text, term):
    """Whole-word match: sentiment words count only as words ("no" is not "now")."""
    return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text) is not None


class FairValueEstimator:
    def __init__(self):
        self.base_rates = {
            "policy_announcement": 0.70,
            "earnings_beat": 0.55,
            "fed_rate": 0.40,
            "sports": 0.60,
            "crypto_price": 0.50,
        }

    def estimate(self, market_type, current_price):
        base = self.base_rates.get(market_type, 0.50)
        edge = current_price - base

        return {
            "base_rate": base,
            "current_price": current_price,
            "overvaluation": edge,
            "contrarian_edge": edge if edge > 0 else 0,
            "direction": "SELL_YES" if edge > 0.05 else (
                "BUY_YES" if edge < -0.05 else "HOLD"
            )
        }


def classify_market(question):
    """Classify a Polymarket question into a category.

    Returns a category string used for base rate lookup
    and insider-info filtering.

    Examples:
      "Will BTC close above $100K on Friday?"     -> "crypto_price"
      "Will the Fed cut rates in June?"            -> "fed_rate"
      "Will Company X beat Q4 earnings?"           -> "earnings_beat"
      "Will Trump win the election?"               -> "politics"
      "Will Team X win the Super Bowl?"            -> "sports"
      "Will [Celebrity] file to run?"              -> "other"
      "Will GDP growth exceed 3%?"                 -> "economic_data"
    """
    q = question.lower()
    if any(_mentions(q, w) for w in ["bitcoin", "btc", "eth", "ethereum", "sol", "solana", "crypto"]):
        return "crypto_price"
    if any(_mentions(q, w) for w in ["fed ", "fomc", "rate cut", "rate hike", "interest rate"]):
        return "fed_rate"
    if any(_mentions(q, w) for w in ["earnings", "revenue", "beat estimates", "quarterly"]):
        return "earnings_beat"
    if any(_mentions(q, w) for w in ["election", "president", "governor", "senator", "vote"]):
        return "politics"
    if any(_mentions(q, w) for w in ["win ", "score", "championship", "playoff", "super bowl"]):
        return "sports"
    if any(_mentions(q, w) for w in ["gdp", "cpi", "inflation", "unemployment", "jobs report"]):
        return "economic_data"
    if any(_mentions(q, w) for w in ["announce", "policy", "regulation", "bill ", "law "]):
        return "policy_announcement"
    return "other"


def extract_primary_keyword(question):
    """Extract the main subject from a market question.

    Used to search Twitter for sentiment about this topic.

    Examples:
      "Will BTC close above $100K?"      -> "bitcoin"
      "Will the Fed cut rates?"          -> "federal reserve"
      "Will Company X beat earnings?"    -> "company x"
    """
    q = question.lower()
    # Map common abbreviations to searchable terms
    keyword_map = {
        "btc": "bitcoin", "eth": "ethereum",
        "sol": "solana", "fed ": "federal reserve",
        "fomc": "FOMC fed", "gdp": "GDP economic",
        "cpi": "CPI inflation",
    }
    for abbr, full in keyword_map.items():
        if _mentions(q, abbr):
            return full

    # Fallback: extract the noun phrase after "Will"
    import re
    match = re.search(r'will\s+(.+?)\s+(close|beat|win|exceed|cut|hit|reach)', q)
    if match:
        return match.group(1).strip()

    # Last resort: first 3 significant words
    stop = {"will", "the", "a", "an", "in", "on", "at", "by", "be"}
    words = [w for w in q.split() if w not in stop][:3]
    return " ".join(words)


def sentiment_score(keyword, tweets, min_tweets=20):
    """Score social media sentiment, weighted by engagement.

    Each tweet's vote is weighted by its engagement
    (likes + retweets + 1). This means a tweet with 500
    likes counts 500x more than a tweet with 0 likes.
    Extreme sentiment from high-engagement tweets is a
    stronger signal than extreme sentiment from nobodies.

    Returns 0.0 (all bearish) to 1.0 (all bullish),
    or 0.50 if fewer than min_tweets are found.
    """
    bull_words = [
        "bullish", "yes", "guaranteed", "100%", "done deal",
        "obvious", "easy money", "no doubt", "lock", "certain",
        "printing", "moon", "lfg", "send it"
    ]
    bear_words = [
        "bearish", "no", "impossible", "never", "no chance",
        "scam", "overvalued", "dump", "rekt", "zero",
        "not happening", "cope", "delusional"
    ]

    bullish_weight = 0
    bearish_weight = 0
    scored_count = 0

    for tweet in tweets:
        text = tweet.get("text", "").lower()
        # Engagement weight: likes + retweets + 1 (minimum 1)
        weight = (
            tweet.get("public_metrics", {}).get("like_count", 0)
            + tweet.get("public_metrics", {}).get("retweet_count", 0)
            + 1
        )

        is_bull = any(_mentions_phrase(text, w) for w in bull_words)
        is_bear = any(_mentions_phrase(text, w) for w in bear_words)

        if is_bull and not is_bear:
            bullish_weight += weight
            scored_count += 1
        elif is_bear and not is_bull:
            bearish_weight += weight
            scored_count += 1

    if scored_count < min_tweets:
        return 0.50  # Not enough data to judge

    total = bullish_weight + bearish_weight
    if total == 0:
        return 0.50

    return bullish_weight / total


def build_base_rates(pages=10):
    """YES rate per category from resolved Yes/No markets.

    Gamma's keyset endpoint pages through closed markets,
    most recently closed first, 100 at a time. A resolved
    market's outcomePrices reads ["1", "0"] if YES won
    and ["0", "1"] if NO won.
    """
    categories = {}
    cursor = None
    for _ in range(pages):
        params = {"closed": "true", "limit": 100,
                  "order": "closedTime", "ascending": "false"}
        if cursor:
            params["after_cursor"] = cursor
        page = requests.get(f"{GAMMA}/markets/keyset",
                            params=params, timeout=30).json()

        for market in page.get("markets", []):
            outcomes = json.loads(market.get("outcomes") or "[]")
            prices = json.loads(market.get("outcomePrices") or "[]")
            if outcomes != ["Yes", "No"] or prices not in (["1", "0"], ["0", "1"]):
                continue                  # not a cleanly resolved Yes/No market
            mtype = classify_market(market["question"])
            c = categories.setdefault(mtype, {"yes": 0, "no": 0, "total": 0})
            c["yes" if prices == ["1", "0"] else "no"] += 1
            c["total"] += 1

        cursor = page.get("next_cursor")
        if not cursor:
            break

    base_rates = {}
    for mtype, counts in categories.items():
        if counts["total"] >= 20:  # Need statistical mass
            rate = counts["yes"] / counts["total"]
            base_rates[mtype] = rate
            print(f"  {mtype}: {rate:.0%} YES "
                  f"({counts['total']} markets)")
    return base_rates

# Usage:
# rates = build_base_rates()
# estimator = FairValueEstimator()
# estimator.base_rates = rates  # Replace hardcoded defaults


class ContrarianBot:
    def __init__(self, portfolio):
        self.portfolio = portfolio
        self.fee_rates = {}  # token_id -> taker rate, for exits
        self.estimator = FairValueEstimator()
        self.min_edge = 0.10  # 10% minimum
        self.max_position_pct = 0.08
        self.avoided_categories = [
            "politics", "policy", "regulation",
            "merger", "acquisition"
        ]
        # Track price history per token for spike detection
        self.price_history = defaultdict(list)

    def update_prices(self, token_id, midpoint):
        """Call each scan cycle to build price history."""
        self.price_history[token_id].append({
            "price": midpoint,
            "timestamp": time.time()
        })
        # Keep last 200 data points (~3 hours at 1/min)
        if len(self.price_history[token_id]) > 200:
            self.price_history[token_id] = (
                self.price_history[token_id][-200:]
            )

    def scan(self, markets, sentiment_data):
        opportunities = []

        for market in markets:
            question = market.get("question", "").lower()

            # Filter: avoid insider-information categories,
            # by classify_market() type and by keyword
            market_type = classify_market(question)
            if (market_type in ("politics", "policy_announcement")
                    or any(cat in question
                           for cat in self.avoided_categories)):
                continue

            # mid of the best bid/ask is the price compared to fair value
            ids = token_ids(market)
            if not ids:
                continue
            token_id = ids[0]

            try:
                bids, asks = get_book(token_id)
                if not bids or not asks:
                    continue
                current_price = (bids[0][0] + asks[0][0]) / 2
            except Exception:
                continue

            # Track price for spike detection
            self.update_prices(token_id, current_price)

            # Estimate fair value
            fv = self.estimator.estimate(
                market_type, current_price
            )

            # sentiment for this market's keyword (0.50 when unknown)
            keyword = extract_primary_keyword(question)
            sentiment = sentiment_data.get(keyword, 0.50)

            # Wait for spike to settle (>10 min since last big move)
            move_age = time_since_last_move(
                self.price_history.get(token_id, [])
            )
            if move_age is not None and move_age < 600:
                continue  # Spike still settling, skip

            # Contrarian signal: price far ABOVE fair value AND the
            # crowd extremely bullish. execute() only ever buys NO, so a
            # bearish crowd here would mean trading with it, not against it.
            if (fv["contrarian_edge"] >= self.min_edge
                    and fv["direction"] == "SELL_YES"
                    and sentiment > 0.85):

                opportunities.append({
                    "market": market,
                    "token_id": token_id,
                    "no_token": ids[1],
                    "fee_rate": get_fee_rate(market),
                    "current_price": current_price,
                    "fair_value": fv["base_rate"],
                    "edge": fv["contrarian_edge"],
                    "sentiment": sentiment,
                    "direction": fv["direction"],
                    "market_type": market_type
                })

        return sorted(
            opportunities,
            key=lambda x: x["edge"],
            reverse=True
        )

    def execute(self, opportunity):
        edge = opportunity["edge"]
        direction = opportunity["direction"]

        # Size based on edge magnitude
        portfolio_value = self.portfolio.total_value({})
        size_pct = min(
            edge * 0.5,  # Bigger edge = bigger position
            self.max_position_pct
        )
        amount = portfolio_value * size_pct

        if direction == "SELL_YES":
            # Buy NO: the market's second clobTokenIds entry,
            # priced off its own book
            bids, asks = get_book(opportunity["no_token"])
            if not asks:
                return
            no_price = asks[0][0]
            shares = int(amount / no_price)
            if shares > 0:
                self.portfolio.buy(
                    opportunity["no_token"],
                    no_price,
                    shares,
                    fee_rate=opportunity["fee_rate"],
                    market_name=(
                        f"CONTRARIAN: "
                        f"{opportunity['market'].get('question', '')[:30]}"
                    )
                )
                print(
                    f"  CONTRARIAN: BUY NO {shares} "
                    f"@ ${no_price:.2f} | "
                    f"Edge: {edge*100:.0f}% | "
                    f"Sentiment: {opportunity['sentiment']*100:.0f}% bullish"
                )
                self.fee_rates[opportunity["no_token"]] = (
                    opportunity["fee_rate"])

    def manage_exits(self, take_profit=0.50):
        """Sell once a position is up 50% after fees (the exit
        in "After the Crowd Calms Down"); else hold to resolution."""
        for token_id, pos in list(self.portfolio.positions.items()):
            try:
                bids, asks = get_book(token_id)
            except requests.RequestException:
                continue        # closed market: no book (404); settlement pays it

            if bids and bids[0][0] >= pos["avg_cost"] * (1 + take_profit):
                self.portfolio.sell(
                    token_id, bids[0][0],
                    fee_rate=self.fee_rates.get(token_id, 0.0))
                print(f"  CONTRARIAN: TAKE PROFIT @ ${bids[0][0]:.2f}")


def collect_sentiment(keywords, news_api_key):
    """Build sentiment scores from recent news headlines.

    Uses the same bull/bear word lists as sentiment_score()
    but applied to news headlines instead of tweets.
    Returns {keyword: score} dict for scan().
    """
    scores = {}
    for keyword in keywords:
        resp = requests.get(
            "https://newsapi.org/v2/everything",
            params={
                "q": keyword,
                "sortBy": "publishedAt",
                "pageSize": 30,
                "language": "en",
                "apiKey": news_api_key
            },
            timeout=5
        )
        articles = resp.json().get("articles", [])

        # Convert articles to tweet-like format for
        # sentiment_score() (it just needs "text" field)
        fake_tweets = [
            {"text": a.get("title", ""),
             "public_metrics": {"like_count": 1,
                                "retweet_count": 0}}
            for a in articles
        ]
        scores[keyword] = sentiment_score(
            keyword, fake_tweets, min_tweets=5
        )

    return scores


def run_contrarian_bot(portfolio=None, news_api_key=None,
                       scan_interval=60, min_edge=0.10, max_position_pct=0.08):
    """Scan, buy and take profits on paper, forever."""
    from scanner import get_markets
    bot = ContrarianBot(portfolio or PaperPortfolio(500.0))
    bot.min_edge, bot.max_position_pct = min_edge, max_position_pct
    sentiment, refreshed = {}, 0
    while True:
        markets = get_markets(limit=20)
        # 20 searches every 6 hours = 80 a day, inside a free
        # NewsAPI key's 100. No key: every score is 0.50.
        if news_api_key and time.time() - refreshed > 6 * 3600:
            sentiment = collect_sentiment(
                {extract_primary_keyword(m["question"])
                 for m in markets}, news_api_key)
            refreshed = time.time()
        for opp in bot.scan(markets, sentiment):
            if opp["no_token"] not in bot.portfolio.positions:
                bot.execute(opp)
        settle_resolved_positions(bot.portfolio)
        bot.manage_exits()
        time.sleep(scan_interval)


def time_since_last_move(price_history, threshold=0.05):
    """Check when the last significant price move happened."""
    if len(price_history) < 10:
        return None

    for i in range(len(price_history) - 1, 0, -1):
        change = abs(
            price_history[i]["price"]
            - price_history[i-1]["price"]
        ) / price_history[i-1]["price"]
        if change >= threshold:
            age_seconds = (
                time.time() - price_history[i]["timestamp"]
            )
            return age_seconds

    return None  # No significant recent move


def main(argv=None):
    parser = argparse.ArgumentParser(description="Paper contrarian bot.")
    parser.add_argument("--balance", type=float, default=500.0, help="paper bankroll (default 500)")
    parser.add_argument("--scan-interval", type=int, default=60, help="seconds between scans (default 60)")
    parser.add_argument("--min-edge", type=float, default=0.10,
                        help="price above base rate needed to trade (default 0.10)")
    parser.add_argument("--max-position-pct", type=float, default=0.08,
                        help="largest position as a share of the portfolio (default 0.08)")
    parser.add_argument("--log", default="contrarian_trades.csv",
                        help="trade log for audit.py (default contrarian_trades.csv)")
    args = parser.parse_args(argv)
    news_key = os.environ.get("NEWS_API_KEY")
    if not news_key:
        print("No NEWS_API_KEY: every sentiment score is 0.50, so the bot scans and never trades.")
    print(f"Scanning the 20 busiest markets every {args.scan_interval} s. "
          f"It prints only when it trades or takes profit.")
    portfolio = PaperPortfolio(args.balance, log_file=args.log)
    bot_settings = {"min_edge": args.min_edge, "max_position_pct": args.max_position_pct}
    try:
        run_contrarian_bot(portfolio, news_key, args.scan_interval, **bot_settings)
    except KeyboardInterrupt:
        print(f"\nStopped. Paper fills are in {args.log}")


if __name__ == "__main__":       # keep this at the bottom of contrarian.py
    main()
