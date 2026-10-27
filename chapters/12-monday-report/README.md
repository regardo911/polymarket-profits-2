# Chapter 12: the Monday routine

The weekly edge report runs inside `portfolio_manager.py` (Chapter 9). The manager checks once a day, and on Mondays it prints each strategy's edge decay and Sharpe, plus any fee, tick-size or neg-risk change Gamma shows since the last snapshot. This chapter's edit (two imports and a new end to `__main__`) is already in the file.

Today, before the manager has a week behind it, you can run the report on fresh slices against live Gamma. Every strategy reads INSUFFICIENT DATA, which is the right answer on day one, and the platform check runs for real:

```bash
cd polymarket-bots
python -c "
from portfolio_manager import PortfolioManager
from utils import PlatformChangeMonitor, weekly_edge_report
from scanner import get_markets
manager = PortfolioManager(1000.0)
for name, pct in [('arbitrage', .25), ('resolution', .20), ('contrarian', .15)]:
    manager.add_strategy(name, None, pct)
monitor = PlatformChangeMonitor()
monitor.check_for_changes(get_markets())
weekly_edge_report(manager, monitor, get_markets())"
```

```
============================================================
WEEKLY EDGE HEALTH REPORT
============================================================

arbitrage: INSUFFICIENT DATA (0 closed trades, need 60)

resolution: INSUFFICIENT DATA (0 closed trades, need 60)

contrarian: INSUFFICIENT DATA (0 closed trades, need 60)

Platform changes this week: none
```

## Every Monday

1. Read the report the manager printed (in `/var/log/polymarket/output.log` on a server).
2. `python audit.py *_trades.csv trades.csv`, one verdict per strategy.
3. Write one line: what you changed because of them. If nothing, write that.

A strategy at 30%+ decay gets Stop, Pivot, Adapt or Add from the chapter; never more capital. A platform change means re-running the edge math on the affected markets before the bot trades them again.

## The first 30 days

| Days | Do |
|---|---|
| 1-7 | everything on paper, `python portfolio_manager.py` running; dashboard daily |
| 8-14 | only if one strategy shows positive EV after fees over 50+ closed trades: that one live with $100-200 (Chapter 4's `live_pair.py` for pairs), the rest on paper |
| 15-21 | the next-best, with its own $100-200, only if the first is profitable with real money |
| 22-28 | review three weeks; add the next performer or don't |
| 30 | full report, correlations from live data, rebalance, set next month's limits |

Stalled? Zero arbitrage gaps for days is normal; check that the closest-gap lines change between passes. Fewer than 50 closed trades after a week: add markets to the trainer (`--markets 20`) or widen one threshold one step. Good paper, bad live: `python audit.py` on both logs and compare the fills.
