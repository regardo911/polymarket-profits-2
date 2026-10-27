"""Resolution scanner: event detection, dispute-aware edge, resolution state machine. Chapter 5.

The price path needs no key: it reads CoinGecko's Bitcoin price and checks
every "Bitcoin above ___" strike in its last minutes. The news path runs only
when NEWS_API_KEY is set (a free key's articles are 24 hours late, so the
staleness check rejects them all).

    python resolution.py 240     # every strike closing in the next 4 hours
    python resolution.py         # the defaults: last 10 minutes
    python resolution.py 60 --min-edge 0.05 --position-size 50

Market data is re-read every pass, and every 5 minutes held positions are
checked: settled ones are listed TO REDEEM and closed at their payout on paper.

Paper trading only. Educational software, not financial advice.
"""
import argparse
import json
import os
import time
from datetime import datetime, timezone
import requests
from scanner import GAMMA, get_markets, get_book, get_fee_rate, token_ids
from arbitrage import get_events, strike
from trainer import PaperPortfolio, settle_resolved_positions


class EventDetector:
    def __init__(self, news_api_key=None):
        self.news_api_key = news_api_key
        self.last_check = time.time()
        self.seen_events = set()

    def last_check_iso(self):
        """Previous check time in the format NewsAPI expects."""
        return datetime.fromtimestamp(
            self.last_check, timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%S")

    def check_news(self, keywords):
        """Check for news matching keywords.

        A free NewsAPI key returns articles 24 hours late and
        allows 100 requests a day: enough to test the parsing,
        never enough to trade on. A paid plan removes the delay.
        """
        if not self.news_api_key:
            return []
        url = "https://newsapi.org/v2/everything"
        params = {
            "q": " OR ".join(keywords),
            "sortBy": "publishedAt",
            "language": "en",
            "apiKey": self.news_api_key,
            "from": self.last_check_iso()
        }
        self.last_check = time.time()

        try:
            resp = requests.get(url, params=params, timeout=5)
            articles = resp.json().get("articles", [])

            new_events = []
            for article in articles:
                event_id = article.get("url", "")
                if event_id not in self.seen_events:
                    self.seen_events.add(event_id)
                    new_events.append({
                        "title": article.get("title", ""),
                        "source": article.get("source", {})
                                        .get("name", ""),
                        "published": article.get("publishedAt"),
                        "description": article.get(
                            "description", ""
                        ) or ""
                    })

            return new_events
        except Exception:
            return []

    def check_price(self, asset):
        """Current USD price from CoinGecko (no key needed)."""
        url = (
            f"https://api.coingecko.com/api/v3/"
            f"simple/price?ids={asset}&vs_currencies=usd"
        )
        try:
            resp = requests.get(url, timeout=5)
            return float(resp.json()[asset]["usd"])
        except Exception:
            return None


def extract_keywords(question):
    """Extract significant words from a market question.

    Drops stop words and short words, returns the terms
    that matter for matching against news headlines.
    """
    stop = {
        "will", "the", "a", "an", "in", "on", "at", "by",
        "be", "to", "of", "is", "it", "for", "and", "or",
        "this", "that", "above", "below", "before", "after"
    }
    words = question.lower().replace("?", "").split()
    return [w for w in words if w not in stop and len(w) > 2]


def match_event_to_markets(event, markets):
    """Find Polymarket markets that this event resolves."""
    matched = []
    event_text = (
        event["title"] + " " + event["description"]
    ).lower()

    for market in markets:
        question = market.get("question", "").lower()

        # Simple keyword matching (upgrade to NLP for production)
        keywords = extract_keywords(question)
        matches = sum(
            1 for kw in keywords
            if kw.lower() in event_text
        )

        if matches >= 2:  # At least 2 keyword matches
            # Predict resolution direction
            direction = predict_direction(
                event_text, question
            )
            matched.append({
                "market": market,
                "direction": direction,
                "confidence": matches / len(keywords),
                "event": event
            })

    return matched

def predict_direction(event_text, question):
    """Predict YES or NO based on event content.

    Handles 4 market types mechanically:
    1. Price-level: "BTC above $100K" + actual price
    2. Economic data: "CPI above 3.0%" + actual number
    3. Threshold: "above/below/exceed" + number
    4. Binary confirmation: "will X happen" + "X happened"
    """
    import re
    q = question.lower()
    text = event_text.lower()

    # Type 1+3: Any "above/exceed/over" threshold market
    above_match = re.search(
        r'(above|exceed|over|higher than)\s+\$?([\d,.]+)\s*(%)?',
        q
    )
    if above_match:
        threshold = float(
            above_match.group(2).replace(",", "")
        )
        is_pct = above_match.group(3) == "%"

        # Find the actual number in the event text
        # Look for numbers near relevant keywords
        numbers = re.findall(
            r'(\d+[,.]?\d*)\s*(%)?', text
        )
        for num_str, pct in numbers:
            val = float(num_str.replace(",", ""))
            # Match percentage to percentage, dollar to dollar
            if is_pct and pct == "%":
                return "YES" if val > threshold else "NO"
            elif not is_pct and not pct and val > 100:
                # Likely a price (BTC, stock, etc.)
                return "YES" if val > threshold else "NO"

    # Type 2: "below/under" threshold (inverse)
    below_match = re.search(
        r'(below|under|less than)\s+\$?([\d,.]+)', q
    )
    if below_match:
        threshold = float(
            below_match.group(2).replace(",", "")
        )
        numbers = re.findall(r'(\d+[,.]?\d*)', text)
        for num_str in numbers:
            val = float(num_str.replace(",", ""))
            if val > 100:  # Likely the relevant number
                return "YES" if val < threshold else "NO"

    # Type 4: Binary "will X happen" + confirmation
    confirm_words = [
        "confirmed", "announced", "approved", "passed",
        "signed", "agreed", "reached", "completed"
    ]
    deny_words = [
        "denied", "rejected", "failed", "collapsed",
        "withdrew", "canceled", "blocked", "vetoed"
    ]
    if any(w in text for w in confirm_words):
        return "YES"
    if any(w in text for w in deny_words):
        return "NO"

    return "UNKNOWN"  # Don't trade if direction unclear


def calculate_edge(market_data, predicted_direction,
                   fee_rate=0.07, dispute_risk_bps=100):
    """Calculate the expected profit from a resolution trade.
    fee_rate: the market's taker rate from get_fee_rate()
              (default: crypto, 0.07)
    dispute_risk_bps: estimated probability of a dispute that
                      reverses your position. 100 bps (1%) is a
                      reasonable default for clean mechanical
                      markets. Raise to 300-500 bps for political
                      or policy markets where disputes are common.
    """
    if predicted_direction == "YES":
        # Buy YES shares, expect $1.00 payout
        entry_price = market_data["ask"]
        expected_payout = 1.00
    elif predicted_direction == "NO":
        # Buy NO shares, expect $1.00 payout
        entry_price = 1.00 - market_data["bid"]  # NO price
        expected_payout = 1.00
    else:
        return None

    gross_edge = expected_payout - entry_price

    # Use actual fee formula, not a flat %.
    # Fee = shares * feeRate * p * (1 - p).
    # For a single share: feeRate * p * (1 - p).
    per_share_fee = fee_rate * entry_price * (1 - entry_price)

    # Expected loss from dispute reversal. If a dispute flips
    # the outcome, your "winning" shares pay $0 instead of $1.
    # Per-share expected loss = P(reversal) × entry_price.
    # This term is what separates a real EV calculation from
    # one that ignores tail risk.
    dispute_rate = dispute_risk_bps / 10000
    per_share_dispute_loss = dispute_rate * entry_price

    net_edge = gross_edge - per_share_fee - per_share_dispute_loss

    return {
        "entry_price": entry_price,
        "expected_payout": expected_payout,
        "gross_edge": gross_edge,
        "per_share_fee": per_share_fee,
        "per_share_dispute_loss": per_share_dispute_loss,
        "net_edge": net_edge,
        "return_pct": net_edge / entry_price * 100
    }


def is_stale(event, max_age_seconds=15):
    """Reject events that are too old to trade on."""
    from datetime import datetime, timezone

    published = datetime.fromisoformat(
        event["published"].replace("Z", "+00:00")
    )
    now = datetime.now(timezone.utc)
    age = (now - published).total_seconds()

    if age > max_age_seconds:
        return True, f"Event is {age:.0f}s old (max {max_age_seconds}s)"
    return False, f"Event is {age:.0f}s old (fresh)"


def validate_resolution_source(market, event=None):
    """Check the market's own resolution source before trading."""
    rules = " ".join([
        market.get("resolutionSource") or "",
        market.get("description") or ""
    ]).lower()
    known_sources = [
        "binance.com", "coingecko", "coinmarketcap",
        "associated press", "reuters", "bls.gov",
        "federalreserve.gov"
    ]
    for source in known_sources:
        if source in rules:
            return True, source
    return False, "Unknown resolution source"


class MarketStateMonitor:
    """Classify a Gamma market's resolution state.

    Gamma carries the UMA trail on every market:
    umaResolutionStatuses is a JSON list such as '[]',
    '["proposed"]' or '["proposed", "proposed", "resolved"]'.
    """

    LIVE = "LIVE"
    ELIGIBLE = "ELIGIBLE"
    IN_WINDOW = "IN_WINDOW"
    DISPUTED = "DISPUTED"
    SETTLED = "SETTLED"

    def classify(self, market):
        statuses = json.loads(
            market.get("umaResolutionStatuses") or "[]"
        )
        if (market.get("umaResolutionStatus") == "resolved"
                or (statuses and statuses[-1] == "resolved")):
            return self.SETTLED
        if statuses.count("proposed") > 1:
            return self.DISPUTED    # more than one proposal on record
        if statuses:
            return self.IN_WINDOW   # a proposal is in its challenge window
        if market.get("closed"):
            return self.IN_WINDOW   # trading stopped, no proposal yet

        end = market.get("endDate")
        if not end:
            return self.LIVE
        end_date = datetime.fromisoformat(end.replace("Z", "+00:00"))
        if datetime.now(timezone.utc) < end_date:
            return self.LIVE
        return self.ELIGIBLE        # past end date, still trading

    def can_enter(self, market):
        """Return (allowed, state, size_multiplier)."""
        state = self.classify(market)
        if not market.get("acceptingOrders"):
            return False, state, 0.0
        if state == self.LIVE:
            return True, state, 1.0
        if state == self.ELIGIBLE:
            return True, state, 0.5   # capital may lock soon
        return False, state, 0.0

    def needs_redemption(self, market):
        return self.classify(market) == self.SETTLED


def check_redemption_queue(portfolio, state_monitor, markets_by_token):
    """List paper positions in settled markets.

    Live wallets redeem with redeem_positions() on the official
    SDK's AsyncSecureClient; winners pay $1.00 in pUSD and there
    is no deadline. Paper mode just logs what would be claimed.
    """
    queue = []
    for token_id in list(portfolio.positions.keys()):
        market = markets_by_token.get(token_id)
        if not market or not state_monitor.needs_redemption(market):
            continue
        shares = portfolio.positions[token_id]["shares"]
        queue.append({"token_id": token_id, "shares": shares,
                      "condition_id": market.get("conditionId", "")})
        print(f"  TO REDEEM: {token_id[:12]}... ({shares} shares)")
    return queue


def minutes_left(market):
    end = datetime.fromisoformat(market["endDate"].replace("Z", "+00:00"))
    return (end - datetime.now(timezone.utc)).total_seconds() / 60




def load_markets():
    """Open markets plus every live "Bitcoin above ___" ladder strike.

    The book loads these once at start. A long run then never sees a new
    hourly ladder, and never sees a held market settle, so the scanner
    re-reads them every pass.
    """
    ladders = [m for ev in get_events(tag_slug="bitcoin")
               if "above ___" in ev.get("title", "")
               for m in ev.get("markets", [])
               if strike(m) and token_ids(m)]
    markets = get_markets(limit=100) + ladders
    return markets, ladders


def index_by_token(markets):
    """Both outcome tokens map to their market, so a NO position is found too."""
    return {tid: m for m in markets for tid in token_ids(m)}


def refresh_held(portfolio, markets_by_token):
    """Re-read the market behind every held token that has closed. A settled
    market drops off the open lists, so it has to be fetched by token id."""
    for token_id in list(portfolio.positions):
        try:
            r = requests.get(f"{GAMMA}/markets",
                             params={"clob_token_ids": token_id, "closed": "true"},
                             timeout=30)
            r.raise_for_status()
        except requests.RequestException:
            continue
        for m in r.json():
            markets_by_token.update(index_by_token([m]))


def run_resolution_scanner(
    news_api_key=None,
    min_edge=0.10,
    max_event_age=15,
    position_size=100.0,
    check_interval=10,
    max_minutes_left=10,
    proxy_buffer=0.01,
    portfolio=None
):
    detector = EventDetector(news_api_key)
    # Chapter 9's portfolio manager passes in its own slice
    portfolio = portfolio or PaperPortfolio(starting_balance=1000.0)
    state_monitor = MarketStateMonitor()

    # Daily and hourly "Bitcoin above ___" ladders
    markets, ladders = load_markets()

    print("=== Resolution Scanner Started ===")
    print(f"Min edge: {min_edge * 100:.0f}% | "
          f"ladder strikes watched: {len(ladders)}")

    last_redemption_check = 0
    last_news_check = 0
    first_pass = True
    while True:
        if not first_pass:
            try:
                markets, ladders = load_markets()
            except requests.RequestException as e:
                print(f"  market refresh failed ({e}), using the last list")
        first_pass = False
        markets_by_token = index_by_token(markets)

        # News path: every 15 minutes (96 calls a day fits a
        # free key's 100, but free articles are 24 hours late,
        # so is_stale() rejects them all; it needs a paid plan)
        if news_api_key and time.time() - last_news_check > 900:
            last_news_check = time.time()
            for event in detector.check_news(
                    ["federal reserve", "cpi", "earnings"]):
                stale, age_msg = is_stale(event, max_event_age)
                if stale:
                    print(f"  SKIP: {age_msg} | {event['title'][:50]}")
                    continue
                for match in match_event_to_markets(event, markets):
                    m = match["market"]
                    allowed, state, size_mult = state_monitor.can_enter(m)
                    source_ok, source = validate_resolution_source(m)
                    if (match["direction"] == "UNKNOWN"
                            or not allowed or not source_ok):
                        print(f"  SKIP: state={state}, source={source}"
                              f" | {m['question'][:40]}")
                        continue
                    yes_token, no_token = token_ids(m)
                    bids, asks = get_book(yes_token)
                    if not bids or not asks:
                        continue
                    edge = calculate_edge(
                        {"ask": asks[0][0], "bid": bids[0][0]},
                        match["direction"], fee_rate=get_fee_rate(m))
                    if edge["net_edge"] >= min_edge:
                        token = (yes_token if match["direction"] == "YES"
                                 else no_token)
                        shares = int(position_size * size_mult
                                     / edge["entry_price"])
                        print(f"  RESOLUTION TRADE: {match['direction']}"
                              f" @ ${edge['entry_price']:.2f} | "
                              f"{m['question'][:50]}")
                        portfolio.buy(token, edge["entry_price"], shares,
                                      market_name=f"RES: {m['question'][:30]}",
                                      fee_rate=get_fee_rate(m))

        # Price path: strikes in their last minutes
        btc = detector.check_price("bitcoin")
        for m in ladders if btc else []:
            left = minutes_left(m)
            if not 0 < left <= max_minutes_left:
                continue
            k = strike(m)
            label = f"above ${k:,.0f}, {left:.0f} min left"
            if btc > k * (1 + proxy_buffer):
                direction = "YES"
            elif btc < k * (1 - proxy_buffer):
                direction = "NO"
            else:
                print(f"  SKIP {label}: BTC ${btc:,.0f} inside "
                      f"the {proxy_buffer:.0%} buffer")
                continue

            allowed, state, size_mult = state_monitor.can_enter(m)
            source_ok, source = validate_resolution_source(m)
            if not allowed or not source_ok:
                print(f"  SKIP {label}: state={state}, source={source}")
                continue

            yes_token, no_token = token_ids(m)
            bids, asks = get_book(yes_token)
            if not bids or not asks:
                print(f"  SKIP {label}: no two-sided book")
                continue
            edge = calculate_edge(
                {"ask": asks[0][0], "bid": bids[0][0]}, direction,
                fee_rate=get_fee_rate(m))
            if edge["net_edge"] < min_edge:
                print(f"  SKIP {label}: {direction} at "
                      f"${edge['entry_price']:.3f}, net edge "
                      f"${edge['net_edge']:.3f} < ${min_edge:.2f}")
                continue

            token = yes_token if direction == "YES" else no_token
            shares = int(position_size * size_mult
                         / edge["entry_price"])
            print(f"  RESOLUTION TRADE: {direction} {shares} @ "
                  f"${edge['entry_price']:.3f} | {label} | "
                  f"net edge ${edge['net_edge']:.3f}")
            portfolio.buy(token, edge["entry_price"], shares,
                          market_name=f"RES: {label}",
                          fee_rate=get_fee_rate(m))

        now = time.time()
        if now - last_redemption_check > 300:
            refresh_held(portfolio, markets_by_token)
            check_redemption_queue(portfolio, state_monitor,
                                   markets_by_token)
            settle_resolved_positions(portfolio)
            last_redemption_check = now

        time.sleep(check_interval)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Paper resolution scanner.")
    parser.add_argument("minutes", nargs="?", type=int, default=10,
                        help="check strikes closing within this many minutes (default 10)")
    parser.add_argument("--min-edge", type=float, default=0.10,
                        help="smallest net edge per share in $ (default 0.10)")
    parser.add_argument("--max-event-age", type=int, default=15,
                        help="seconds before a news event counts as stale (default 15)")
    parser.add_argument("--position-size", type=float, default=100.0, help="$ per trade (default 100)")
    parser.add_argument("--check-interval", type=int, default=10, help="seconds between passes (default 10)")
    parser.add_argument("--proxy-buffer", type=float, default=0.01,
                        help="skip strikes this close to the CoinGecko price (default 0.01 = 1%%)")
    parser.add_argument("--balance", type=float, default=1000.0, help="paper bankroll in $ (default 1000)")
    parser.add_argument("--log", default="resolution_trades.csv",
                        help="trade log for audit.py (default resolution_trades.csv)")
    args = parser.parse_args(argv)
    news_key = os.environ.get("NEWS_API_KEY")
    print("News path: " + ("on (NEWS_API_KEY set)" if news_key
                           else "off, no NEWS_API_KEY. The price path runs without it."))
    portfolio = PaperPortfolio(args.balance, log_file=args.log)
    try:
        run_resolution_scanner(news_key, args.min_edge, args.max_event_age,
                               args.position_size, args.check_interval,
                               args.minutes, args.proxy_buffer, portfolio)
    except KeyboardInterrupt:
        print(f"\nStopped. Paper fills are in {args.log}")


if __name__ == "__main__":
    main()
