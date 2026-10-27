# Chapter 11: check a profit claim in 30 seconds

```bash
cd polymarket-bots
python verify_claim.py 0x5924ca480d8b08cd5f3e5811fa378c4082475af6 178000
```

```
Trades on record:    19,216
Claimed profit:      $178,000
Realized profit:     $150,686
Deposits on record:  not reported
  Month  1: $+107,514
  Month  2: $+43,171
  Month  3: $+0
  ...
Last activity:       186 days ago
Claim minus record:  $+27,314
```

Real money once, no edge now, nothing to copy. Your "days ago" will be larger.

Swap in the wallet and the number from any post you see. Only a profile name? `https://gamma-api.polymarket.com/public-search?q=<name>&search_profiles=true` returns its `proxyWallet`. Then score it against the chapter's red flags, and write down the claimed profit, the realized profit, the months, the deposit status and the days since its last trade.
