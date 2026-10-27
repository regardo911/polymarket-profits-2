# Chapter 7: the Claude agent

This bot runs on your own Anthropic API key, and every cycle is billed to it. One cycle: Claude checks the paper portfolio, scans live markets, reads order books, prices and news through six tools, writes down why, then paper-trades or says why it won't.

```bash
cd polymarket-bots
export ANTHROPIC_API_KEY=...         # yours
python claude_agent.py
```

At the end it prints the tokens it used and the cycle's cost, both read from the API's own usage numbers:

```
Tokens: <yours> in / <yours> out over <n> requests
Cycle cost: $<yours>
Reasoning and tool calls: agent_log.jsonl
```

`agent_log.jsonl` holds one line per event: each tool call with its input, each result, each reasoning block, any paper trade with its reasoning, and the cycle's tokens and cost. That's the log Appendix C, Walkthrough 5 reconciles against the trade log.

The chapter's checkpoint: 3 or more markets analyzed with reasoning you can read, and at least one paper trade with its justification logged. Read the reasoning line by line. Weak reasoning on a winning trade is luck; fix the prompt anyway.

## The guardrails you can't talk it out of

`validate_trade()` runs in Python before every paper trade: no trade over $50, no fourth open position, nothing while the portfolio is more than 15% below its peak, and nothing on a market that is closed or not accepting orders. The prompt asks for the rest (10% edge, 5-10% sizing, $1,000 depth, no market within 5 minutes of resolving). Every 50 trades, read the five worst losses and add a rule to `AGENT_SYSTEM_PROMPT`.

## Comparing models

```bash
python claude_agent.py --model claude-haiku-5-5 --input-price 0.10 --output-price 0.50
```

Prices live in `PRICES_PER_MILLION` next to the model name; only Sonnet 5.5 ($2 in, $10 out per million tokens) ships there. Swap to a model with no price on file and the cost line reads `unknown, you must observe this` until you pass that model's prices from Anthropic's pricing page.

The agent's tool code and its guardrails run in the offline tests with a stand-in client; Claude's own decisions can only be seen by running it on your key.
