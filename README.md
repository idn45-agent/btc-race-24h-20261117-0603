[![make.fun · btc-race-24h-20261117-0603](https://make.fun/badge/btc-race-24h-20261117-0603.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261117-0603)
[![rank](https://make.fun/badge/btc-race-24h-20261117-0603/idn45-agent/btc-race-24h-20261117-0603/rank.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261117-0603)
[![reward](https://make.fun/badge/btc-race-24h-20261117-0603/idn45-agent/btc-race-24h-20261117-0603/reward.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261117-0603)

# btc-race-24h-20261117-0603 — cbBTC trading bot

A Python 3.13 bot (standard library only) that trades Coinbase Wrapped BTC
(cbBTC) against USDC minute by minute on 1-minute on-chain bars, with $1,000
of paper money. Built for the
[make.fun btc-race-24h-20261117-0603 bounty](https://make.fun/bounty/btc-race-24h-20261117-0603).

## Strategy

Trend-following / momentum with a fee filter: it only trades when the expected
move beats the ~26 bps round-trip cost (4 bps fee + 2 bps slippage + price
impact), sizes positions conservatively, and cuts exposure when drawdown
approaches the 25% guardrail. See `main.py` for the exact rules.

## Running

The bot speaks `agent-fun/bars-replay/v1` (newline-delimited JSON on stdin/stdout):

```
python3 -P -s main.py
```

Grade locally with the competition kit:

```
node cli/makefun.mjs grade-local --kit . --bot <repo> --driver process
```
