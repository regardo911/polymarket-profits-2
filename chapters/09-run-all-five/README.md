# Chapter 9: all five on paper, one bankroll

```bash
cd polymarket-bots
python portfolio_manager.py
python portfolio_manager.py --capital 2000 --target-wallet 0x<wallet> --target-value 30000
```

Each strategy gets its own thread and its own paper slice of one $1,000 bankroll: arbitrage 25%, resolution 20%, copy 15%, Claude 10%, contrarian 15%, and 15% stays in cash. With no keys and no wallet set, the first lines look like this (a real start):

```
=== Arbitrage Scanner Started ===
Min gap after fees: $0.005 | max $50 per pair trade
copy: TARGET_WALLET is not a wallet address, copy bot not started
claude: ANTHROPIC_API_KEY not set, agent not started
=== Resolution Scanner Started ===
Min edge: 10% | ladder strikes watched: 117
```

Three threads keep running. Give it a wallet (Chapter 6) to start the copier, `ANTHROPIC_API_KEY` to start the agent (billed to you every 15 minutes), and `NEWS_API_KEY` so the contrarian bot can trade.

The dashboard and the kill-switch alerts print once an hour, so the first one arrives an hour in. Daily returns are recorded every 24 hours. The weekly report runs on Mondays (Chapter 12). Once a week `rebalance()` moves paper cash; each slice ends between 5% and 35%, and the 15% reserve stays. The checkpoint is a dashboard with every strategy's line and the combined value, reserve included. After 14 days of returns the correlation table fills in.

The alerts print; they don't stop anything. When `PAUSE ALL` or `HALT ALL` shows, stop the process yourself and look.

Each slice writes its own log (`arbitrage_trades.csv`, `resolution_trades.csv`, `copy_trades.csv`, `claude_trades.csv`, `contrarian_trades.csv`), so Chapter 10's audit works per strategy.

## On a server

[`polymarket.conf`](polymarket.conf) is the book's supervisord program, with keys left empty:

```bash
sudo cp polymarket.conf /etc/supervisor/conf.d/polymarket.conf   # then type your keys between the quotes
sudo chmod 600 /etc/supervisor/conf.d/polymarket.conf
sudo mkdir -p /var/log/polymarket && sudo supervisorctl reread && sudo supervisorctl update && sudo supervisorctl start portfolio_manager
tail -f /var/log/polymarket/output.log
```

Run the `cp` from this folder. The file expects the code at `/home/user/polymarket-bots` with its venv at `.venv` inside it; in a clone of this repo that folder is `<clone>/polymarket-bots`, so change `command=` and `directory=` to match. It parses under supervisor 4.3.0. A key you don't have stays as `""`.
