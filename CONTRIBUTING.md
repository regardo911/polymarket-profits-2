# Contributing

Fixes are welcome, especially when Polymarket changes something under the bots: a renamed field, a new fee rule, an endpoint that moved. Those break things quietly, and a reader who hits one first is the best person to report it.

Every file in `polymarket-bots/` is a file the book has you build, in the book's names. Keep it that way, so a reader who followed the chapters recognizes what they copy. A pull request that renames a printed function or changes a printed default needs a reason from the book or from a failing run.

Before you open one:

```bash
python -m unittest discover tests      # offline; no keys, no network
```

If you found a place where the printed book doesn't work as written, add it to `BOOK-FIXES.md` with the chapter and the output that shows it failing.

Please don't open a pull request that adds an order path. `live_pair.py` is the one place orders exist, and it stays off unless the reader edits the code.
