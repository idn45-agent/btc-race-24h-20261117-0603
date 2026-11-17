"""cbBTC trend bot for make.fun btc-race-24h-20261117-0603.

Speaks agent-fun/bars-replay/v1: the harness sends hello once, then one
batch per 1-minute bar of cbBTC (CBBTC/USDC), then done. This bot answers
every batch with exactly one reply echoing its seq, carrying its orders
(buy/sell) for that bar; orders decided on a bar fill at the next bar's open.

Strategy (v2): hold with crash protection. The dev window is choppy at the
1-minute scale (1-min return stdev ~6.4 bps) while its real moves are hourly
(60-min moves up to ~200 bps), and a full-size round trip costs ~26 bps
(4 bps fee + 2 bps slippage + price impact per leg), so fast EMA crosses
whipsaw: v1 lost money on 22 trades. v2 therefore trades rarely: it buys
once the slow regime is bullish (close above its long moving average with a
positive trailing return) and holds through the noise, exiting only when the
regime flips bearish, a trailing stop is hit, or a hard stop is hit. A
drawdown guard halts new entries well before the 25% guardrail.
Deterministic: floats only, no clock, no environment, no unseeded
randomness; the same bars give the same orders every run.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

SYMBOL = "CBBTC"

# --- Strategy parameters (tuned on the frozen dev window; see builder log) ---
SMA_SPAN = 120          # long moving-average span, in bars (minutes)
MOM_LOOKBACK = 60       # trailing-return lookback, in bars
MOM_MIN_BPS = 30        # entry needs trailing return above this (bps)
TRAIL_BPS = 120         # trailing stop: exit if close falls this far under the peak since entry
HARD_STOP_BPS = 150     # hard stop: exit if position value falls this far under its cost
COOLDOWN_BARS = 30      # bars to wait after an exit before a new entry
HALT_DD_BPS = 1000      # halt new entries once equity drawdown passes this (10%)
EXIT_DD_BPS = 1500      # exit the position once equity drawdown passes this (15%)
NO_ENTRY_LAST_BARS = 30  # do not open a position this close to the end of the window
MAX_SLIPPAGE_BPS = 100  # 1% past the close the order was decided on

BPS = 10_000


class Bot:
    def __init__(self):
        self.closes = []          # closes, oldest first
        self.peak_close = None    # highest close since the current entry
        self.peak_equity = None   # highest equity seen (for the drawdown guard)
        self.cooldown_until = -1  # seq before which no new entry

    def _sma(self, span):
        if len(self.closes) < span:
            return None
        return sum(self.closes[-span:]) / span

    def _momentum_bps(self):
        if len(self.closes) <= MOM_LOOKBACK:
            return None
        then = self.closes[-1 - MOM_LOOKBACK]
        now = self.closes[-1]
        return (now - then) * BPS / then

    def on_batch(self, state, batch):
        orders = []
        bar = None
        for b in batch["bars"]:
            if b["symbol"] == SYMBOL:
                bar = b
                break
        if bar is None:
            return orders  # no bar for our symbol this minute: nothing to decide on
        close = int(bar["c"])
        self.closes.append(close)

        # Drawdown guard runs on the account equity the batch carries.
        if self.peak_equity is None or state.equity > self.peak_equity:
            self.peak_equity = state.equity
        dd_bps = (self.peak_equity - state.equity) * BPS / self.peak_equity

        if state.warming_up:
            return orders
        sma = self._sma(SMA_SPAN)
        mom = self._momentum_bps()
        if sma is None or mom is None:
            return orders

        held = state.positions.get(SYMBOL)
        if held is not None:
            if self.peak_close is None or close > self.peak_close:
                self.peak_close = close
            value = state.value(SYMBOL, held.qty)
            regime_bearish = close < sma
            trail_hit = (close * BPS) < self.peak_close * (BPS - TRAIL_BPS)
            hard_hit = value * BPS < held.cost * (BPS - HARD_STOP_BPS)
            dd_hit = dd_bps >= EXIT_DD_BPS
            if regime_bearish or trail_hit or hard_hit or dd_hit:
                orders.append({"side": "sell", "symbol": SYMBOL, "qty": held.qty,
                               "max_slippage_bps": MAX_SLIPPAGE_BPS})
                self.cooldown_until = state.seq + COOLDOWN_BARS
                self.peak_close = None
        else:
            self.peak_close = None
            near_end = state.seq >= 1440 - NO_ENTRY_LAST_BARS
            regime_bullish = close > sma and mom > MOM_MIN_BPS
            if regime_bullish and state.seq >= self.cooldown_until \
                    and dd_bps < HALT_DD_BPS and not near_end:
                room = state.weight_room(SYMBOL)
                amount = state.cash if state.cash < room else room
                if amount >= int(state.hello["fees"]["min_notional_quote_units"]):
                    orders.append({"side": "buy", "symbol": SYMBOL,
                                   "notional_quote_units": amount,
                                   "max_slippage_bps": MAX_SLIPPAGE_BPS})
                    self.peak_close = close
        return orders


BOT = Bot()


def on_batch(state, batch):
    return BOT.on_batch(state, batch)


makefun_bars.run(on_batch)
