"""A Claude tool-use trading agent with hard Python guardrails, on paper. Chapter 7.

This is the one bot that needs your own Anthropic API key, and every cycle is
billed to it. Each cycle reads live markets through six tools, writes its
reasoning, and paper-trades through validate_trade(): $50 cap, 3 open
positions, stop at a 15% drawdown, no closed markets. Every tool call, result,
reasoning block and the cycle's token count land in agent_log.jsonl.

    export ANTHROPIC_API_KEY=...        # yours
    python claude_agent.py              # one cycle on a $100 paper portfolio
    python claude_agent.py --model claude-haiku-5-5 --input-price 0.10 --output-price 0.50

The cycle cost is token counts read from the API response times the per-million
prices in PRICES_PER_MILLION. A model with no price there prints
"unknown, you must observe this" until you pass its prices.

This is plain Messages API tool use, not an MCP server.
Paper trading only. Educational software, not financial advice.
"""
import argparse
import json
import os
import time
import uuid
import requests
import anthropic
from scanner import GAMMA, get_markets, get_book, get_fee_rate, token_ids
from copy_trader import market_for_token

MODEL = "claude-sonnet-5-5"
# $ per million tokens (input, output). Kept next to MODEL so a model swap
# (Chapter 12, "Cheaper models") can't print the old model's price.
PRICES_PER_MILLION = {
    "claude-sonnet-5-5": (2.0, 10.0),   # Sonnet 5.5: $2 in, $10 out
}
AGENT_LOG = "agent_log.jsonl"

# The six tools Claude may call: market data, context, and one trade tool
market_tools = [
    {
        "name": "get_market_data",
        "description": (
            "Get the best bid, best ask, order book depth "
            "at the top 5 levels, fee rate, and whether a "
            "Polymarket market is still accepting orders."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "token_id": {
                    "type": "string",
                    "description": "The Polymarket token ID"
                }
            },
            "required": ["token_id"]
        }
    },
    {
        "name": "list_markets",
        "description": (
            "List open Polymarket markets, optionally "
            "filtered by keyword. Returns market question, "
            "YES token ID, YES price, end date, 24h volume "
            "and fee rate."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": (
                        "Filter markets by keyword "
                        "(e.g., 'bitcoin', 'fed')"
                    )
                },
                "limit": {
                    "type": "integer",
                    "description": "Max markets to return",
                    "default": 10
                }
            }
        }
    },
    {
        "name": "get_portfolio",
        "description": (
            "Get current paper portfolio status: cash, "
            "open positions, total value, and P&L."
        ),
        "input_schema": {
            "type": "object",
            "properties": {}
        }
    }
]


context_tools = [
    {
        "name": "check_news",
        "description": (
            "Search news headlines about a topic. Returns "
            "headlines, sources, and publication times. On "
            "a free NewsAPI key, articles are 24 hours late: "
            "treat them as background, not breaking news."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "News search query"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "get_price_data",
        "description": (
            "Get current and recent price data for a "
            "cryptocurrency. Returns current price, "
            "1h change, and recent price history."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "asset": {
                    "type": "string",
                    "description": (
                        "Asset name (e.g., 'bitcoin', "
                        "'ethereum')"
                    )
                }
            },
            "required": ["asset"]
        }
    }
]


trading_tools = [
    {
        "name": "paper_trade",
        "description": (
            "Execute a paper trade on a Polymarket market. "
            "Simulates the trade with no real money at risk. "
            "Returns the trade result with P&L impact."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "token_id": {
                    "type": "string",
                    "description": (
                        "The market's YES token ID from "
                        "list_markets (BUY_NO / SELL_NO "
                        "trade its NO token)"
                    )
                },
                "side": {
                    "type": "string",
                    "enum": ["BUY_YES", "BUY_NO",
                             "SELL_YES", "SELL_NO"],
                    "description": "Trade direction"
                },
                "amount_usd": {
                    "type": "number",
                    "description": (
                        "Dollar amount to trade (max $50)"
                    )
                },
                "reasoning": {
                    "type": "string",
                    "description": (
                        "Why this trade makes sense "
                        "(required for logging)"
                    )
                }
            },
            "required": [
                "token_id", "side",
                "amount_usd", "reasoning"
            ]
        }
    }
]


AGENT_SYSTEM_PROMPT = """You are a Polymarket trading agent.
You analyze prediction markets and make paper trades
when you identify an edge.

DECISION FRAMEWORK:
1. Check the portfolio first. Know your current positions
   and cash.
2. Scan for markets where the price diverges from your
   assessment of the true probability.
3. For each candidate trade, check news and external data
   to validate your thesis.
4. Only trade when your estimated edge exceeds 10%.
   Edge = your probability estimate minus the market price.
5. Size positions at 5-10% of available cash. Never go
   all-in on a single market.
6. Always explain your reasoning before trading. The
   reasoning field is not optional.

WHAT COUNTS AS AN EDGE:
- News that the market hasn't priced in yet (check
  publication time vs. last price movement)
- Price discrepancies between correlated markets
  (same event, different time frames)
- Crowd overreaction (price moved 15%+ on news that
  doesn't warrant 15%+)
- Resolution approaching with price not converging to
  0 or 1

WHAT DOES NOT COUNT AS AN EDGE:
- "I think this event is likely" without supporting data
- Price momentum ("it's been going up so it will keep
  going up")
- Gut feeling
- Consensus opinion ("everyone thinks this will happen")

RISK RULES:
- Never trade markets where closed is true or
  accepting_orders is false
- Maximum 3 open positions at any time
- Maximum 10% of portfolio in any single market
- Do not trade markets resolving within 5 minutes
  (too volatile)
- Do not trade markets with less than $1,000 in order
  book depth (too thin)
- If portfolio is down more than 15% from peak, stop
  trading and report status
"""


def log_agent_event(event):
    """Append one JSON line to AGENT_LOG. Returns False if it can't be written."""
    event = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), **event}
    try:
        with open(AGENT_LOG, "a") as f:
            f.write(json.dumps(event, default=str) + "\n")
        return True
    except OSError:
        return False


def cycle_cost(model, input_tokens, output_tokens, prices=None):
    """$ for one cycle, or None when the model's prices aren't known."""
    prices = prices or PRICES_PER_MILLION.get(model)
    if not prices:
        return None
    return (input_tokens * prices[0] + output_tokens * prices[1]) / 1_000_000


def run_trading_agent(portfolio, news_api_key=None, model=MODEL, prices=None):
    api_client = anthropic.Anthropic()   # reads ANTHROPIC_API_KEY
    all_tools = market_tools + context_tools + trading_tools
    cycle = uuid.uuid4().hex[:8]
    input_tokens = output_tokens = requests_made = 0

    messages = [{
        "role": "user",
        "content": (
            "Analyze the current Polymarket markets. "
            "Check your portfolio, scan for interesting "
            "markets, and make trades if you find an edge. "
            "Explain your reasoning for every decision."
        )
    }]
    log_agent_event({"cycle": cycle, "event": "start", "model": model})

    while True:
        response = api_client.messages.create(
            model=model,
            max_tokens=4096,
            system=AGENT_SYSTEM_PROMPT,
            tools=all_tools,
            messages=messages
        )
        requests_made += 1
        input_tokens += response.usage.input_tokens
        output_tokens += response.usage.output_tokens

        # tool_use: run each tool, send every result back in one message
        if response.stop_reason == "tool_use":
            tool_results = []

            for block in response.content:
                if block.type == "tool_use":
                    log_agent_event({"cycle": cycle, "event": "tool_call",
                                     "tool": block.name, "input": block.input})
                    try:
                        result = execute_tool(
                            block.name,
                            block.input,
                            portfolio,
                            news_api_key
                        )
                    except Exception as e:
                        result = {
                            "error": f"{block.name} failed: {e}"
                        }
                        print(f"  Tool error: {e}")
                    log_agent_event({"cycle": cycle, "event": "tool_result",
                                     "tool": block.name, "result": result})
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result)
                    })

                elif block.type == "text":
                    # Agent's reasoning (print it)
                    print(f"\nAgent: {block.text}")
                    log_agent_event({"cycle": cycle, "event": "reasoning",
                                     "text": block.text})

            messages.append({
                "role": "assistant",
                "content": response.content
            })
            messages.append({
                "role": "user",
                "content": tool_results
            })

        else:
            # Agent is done reasoning
            for block in response.content:
                if block.type == "text":
                    print(f"\nAgent: {block.text}")
                    log_agent_event({"cycle": cycle, "event": "reasoning",
                                     "text": block.text})
            cost = cycle_cost(model, input_tokens, output_tokens, prices)
            print(f"\nTokens: {input_tokens:,} in / {output_tokens:,} out "
                  f"over {requests_made} requests")
            if cost is None:
                print(f"Cycle cost: unknown, you must observe this "
                      f"(no price on file for {model})")
            else:
                print(f"Cycle cost: ${cost:.4f}")
            log_agent_event({"cycle": cycle, "event": "end", "model": model,
                             "requests": requests_made,
                             "input_tokens": input_tokens,
                             "output_tokens": output_tokens,
                             "cost_usd": cost if cost is not None
                             else "unknown, you must observe this"})
            break

def execute_tool(name, params, portfolio, news_key):
    """Execute a tool call and return the result."""
    if name == "list_markets":
        keyword = params.get("keyword", "").lower()
        limit = params.get("limit", 10)
        if keyword:                    # Gamma search, open events
            found = requests.get(
                f"{GAMMA}/public-search",
                params={"q": keyword, "events_status": "active"},
                timeout=30).json()
            markets = [m for ev in found.get("events") or []
                       for m in ev.get("markets", [])]
        else:
            markets = get_markets(limit=100)
        markets = [m for m in markets
                   if m.get("acceptingOrders") and token_ids(m)]
        results = []
        for m in markets[:limit]:
            prices = json.loads(m.get("outcomePrices") or "[]")
            results.append({
                "question": m["question"],
                "token_id": token_ids(m)[0],      # the YES token
                "yes_price": prices[0] if prices else None,
                "end_date": m.get("endDate", ""),
                "volume_24h": round(float(m.get("volume24hr") or 0)),
                "fee_rate": get_fee_rate(m),
            })
        return {"markets": results}

    elif name == "get_market_data":
        token_id = params["token_id"]
        market = market_for_token(token_id)
        bids, asks = get_book(token_id)            # best first
        return {
            "question": market.get("question", ""),
            "accepting_orders": bool(market.get("acceptingOrders")),
            "closed": bool(market.get("closed")),
            "end_date": market.get("endDate", ""),
            "fee_rate": get_fee_rate(market),
            "bid": bids[0][0] if bids else None,
            "ask": asks[0][0] if asks else None,
            "total_bid_depth_usd": round(sum(p * s for p, s in bids[:5]), 2),
            "total_ask_depth_usd": round(sum(p * s for p, s in asks[:5]), 2),
            "bid_depth": [{"price": p, "size": s} for p, s in bids[:5]],
            "ask_depth": [{"price": p, "size": s} for p, s in asks[:5]],
        }

    elif name == "get_portfolio":
        return portfolio.stats({})

    elif name == "check_news":
        resp = requests.get(
            "https://newsapi.org/v2/everything",
            params={"q": params["query"], "sortBy": "publishedAt",
                    "pageSize": 5, "apiKey": news_key},
            timeout=10)
        articles = resp.json().get("articles", [])
        return {
            "note": "Free NewsAPI keys return articles 24 hours late.",
            "articles": [{"title": a.get("title", ""),
                          "source": a.get("source", {}).get("name"),
                          "published": a.get("publishedAt", "")}
                         for a in articles],
        }

    elif name == "get_price_data":
        asset = params["asset"].lower()
        resp = requests.get(
            f"https://api.coingecko.com/api/v3/coins/{asset}/market_chart",
            params={"vs_currency": "usd", "days": 1}, timeout=10)
        prices = resp.json().get("prices", [])     # 5-minute points
        if not prices:
            return {"error": f"No price data for {asset}"}
        current = prices[-1][1]
        hour_ago = prices[-13][1] if len(prices) > 12 else current
        return {
            "asset": asset,
            "price_usd": round(current, 2),
            "change_1h_pct": round((current - hour_ago) / hour_ago * 100, 2),
            "prices_last_6h": [{"time": p[0], "price": round(p[1], 2)}
                               for p in prices[-72::12]],
        }

    elif name == "paper_trade":
        ok, problems = validate_trade(params, portfolio)
        if not ok:
            return {"success": False, "blocked": problems}
        market = market_for_token(params["token_id"])
        if market.get("closed") or not market.get("acceptingOrders"):
            return {"success": False, "blocked": ["Market is closed"]}
        yes_token, no_token = token_ids(market)
        token = no_token if params["side"].endswith("NO") else yes_token
        bids, asks = get_book(token)
        fee_rate = get_fee_rate(market)
        if params["side"].startswith("BUY"):
            if not asks:
                return {"error": "No asks in order book"}
            price = asks[0][0]                     # best ask
            shares = int(min(params["amount_usd"], 50) / price)
            success = portfolio.buy(
                token, price, shares, fee_rate=fee_rate,
                market_name=f"CLAUDE: {params['reasoning'][:30]}")
        else:
            if not bids:
                return {"error": "No bids in order book"}
            price = bids[0][0]                     # best bid
            shares = int(min(params["amount_usd"], 50) / price)
            success = portfolio.sell(token, price, shares,
                                     fee_rate=fee_rate)
        # Report what the paper portfolio actually filled: a buy can be
        # scaled down to the cash on hand and a sell is capped at the shares
        # held, so the requested count can be wrong.
        if success:
            shares = portfolio.trade_log[-1]["shares"]
        logged = log_agent_event({
            "event": "paper_trade", "token_id": token, "side": params["side"],
            "amount_usd": params["amount_usd"], "reasoning": params["reasoning"],
            "success": success, "shares": shares if success else 0, "price": price})
        return {"success": success, "shares": shares if success else 0,
                "price": price, "reasoning_logged": logged}

    return {"error": f"Unknown tool: {name}"}


# execute_tool's paper_trade branch calls this first:
def validate_trade(params, portfolio):
    """Hard-coded safety checks the agent cannot override."""
    checks = []

    # Max position size
    if params["amount_usd"] > 50:
        checks.append("Position exceeds $50 limit")

    # Max open positions
    if len(portfolio.positions) >= 3 and "BUY" in params["side"]:
        checks.append("Already at 3 open positions")

    # Portfolio drawdown check: the drop from the peak right now
    # ("down more than 15% from peak" in the system prompt)
    stats = portfolio.stats({})
    if stats.get("drawdown", 0) > 15:
        checks.append("Portfolio drawdown exceeds 15%")

    if checks:
        return False, checks
    return True, []


def main(argv=None):
    global AGENT_LOG
    parser = argparse.ArgumentParser(
        description="One agent cycle on your own Anthropic key (billed per cycle).")
    parser.add_argument("--balance", type=float, default=100.0, help="paper bankroll (default 100)")
    parser.add_argument("--model", default=MODEL, help=f"Claude model id (default {MODEL})")
    parser.add_argument("--input-price", type=float, help="$ per million input tokens, for a model not in PRICES_PER_MILLION")
    parser.add_argument("--output-price", type=float, help="$ per million output tokens")
    parser.add_argument("--log", default=AGENT_LOG, help=f"JSON-lines cycle log (default {AGENT_LOG})")
    parser.add_argument("--trades", default="claude_trades.csv",
                        help="trade log for audit.py (default claude_trades.csv)")
    args = parser.parse_args(argv)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("claude_agent.py runs on your own Anthropic key: "
                         "export ANTHROPIC_API_KEY=... first. Each cycle is billed to that key.")
    if (args.input_price is None) != (args.output_price is None):
        raise SystemExit("pass both --input-price and --output-price, or neither")
    prices = None if args.input_price is None else (args.input_price, args.output_price)

    AGENT_LOG = args.log
    from trainer import PaperPortfolio
    run_trading_agent(PaperPortfolio(args.balance, log_file=args.trades),
                      os.environ.get("NEWS_API_KEY"), args.model, prices)
    print(f"Reasoning and tool calls: {args.log}")


if __name__ == "__main__":
    main()
