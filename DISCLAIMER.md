# Disclaimer

> **⚠️ Not financial advice.** This repository is **educational software** that accompanies the book *Polymarket Profits 2 - Build 7 Trading Bots This Weekend*. It is provided for learning and research only. None of it is financial advice or a recommendation to buy or sell any contract. Trading prediction markets carries substantial risk of losing all the money you put in. Any figures this code produces, from paper trades on live prices or from the made-up test data, do **not** predict real results and are **not** indicative of any real outcome. The authors and contributors make **no warranty** of any kind and accept **no liability** for any loss arising from use of this software. **The software defaults to safe (paper) mode; enabling any live behavior is entirely at your own risk.** You are solely responsible for your own decisions and for complying with the laws, regulations, and third-party terms that apply to you. Consult a licensed professional before acting.

Specific to this repo:

Every bot trades on paper. Its prices are live, its fills are pretend: a paper fill lands at the best bid or ask with no slippage, so paper profit is an upper bound on what the same trades would earn with money.

Where you may trade live depends on your country. On polymarket.com the United States is close-only; US residents use Polymarket US or Kalshi. Check `GET https://polymarket.com/api/geoblock` before any order code, and don't use a VPN or a false location to get around a block. Don't trade a market on information you got through privileged access.

This project is unofficial and not affiliated with Polymarket.
