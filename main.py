"""cbBTC dip-buy bot for make.fun btc-race-24h-20261117-0603.

Speaks agent-fun/bars-replay/v1: the harness sends hello once, then one
batch per 1-minute bar of cbBTC (CBBTC/USDC), then done. This bot answers
every batch with exactly one reply echoing its seq, carrying its orders
(buy/sell) for that bar; orders decided on a bar fill at the next bar's open.

Strategy (v3): mean reversion on the hourly scale. The dev window is choppy
at the 1-minute scale (1-min return stdev ~6.4 bps) and a full-size round
trip costs ~26 bps (4 bps fee + 2 bps slippage + price impact per leg), so
fast trend signals whipsaw: v1 (EMA cross, 22 trades) lost $44.74 and v2
(SMA regime, 10-22 trades) lost $29-45. The window instead mean-reverts on
the hourly scale: sharp 60-minute drops reverse. So v3 trades rarely: it
buys after the trailing 60-bar return falls below -100 bps, and sells when
the trailing 60-bar return recovers above +100 bps, or on a hard stop 150 bps
under the entry close. On the frozen dev window that is 3 round trips for
about +$24.5 after fees, with max drawdown under 1%. A drawdown guard halts
new entries well before the 25% guardrail. Deterministic: floats only, no
clock, no environment, no unseeded randomness; the same bars give the same
orders every run.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

SYMBOL = "CBBTC"

# --- Strategy parameters (tuned on the frozen dev window; see builder log) ---
LOOKBACK = 60          # trailing-return lookback, in bars (minutes)
DROP_BPS = 100         # entry: trailing return below this (bps, negative)
RECOVER_BPS = 100      # exit: trailing return above this (bps, positive)
STOP_BPS = 150         # hard stop: exit if close falls this far under the entry close
HALT_DD_BPS = 1000     # halt new entries once equity drawdown passes this (10%)
EXIT_DD_BPS = 1500     # exit the position once equity drawdown passes this (15%)
NO_ENTRY_LAST_BARS = 30  # do not open a position this close to the end of the window
MAX_SLIPPAGE_BPS = 100  # 1% past the close the order was decided on

BPS = 10_000


class Bot:
    def __init__(self):
        self.closes = []          # closes, oldest first
        self.entry_close = None   # close of the bar the current position was decided on
        self.peak_equity = None   # highest equity seen (for the drawdown guard)

    def _trailing_bps(self):
        """Trailing return over LOOKBACK bars, in bps (None if too few bars)."""
        if len(self.closes) <= LOOKBACK:
            return None
        then = self.closes[-1 - LOOKBACK]
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
        ret = self._trailing_bps()
        if ret is None:
            return orders

        held = state.positions.get(SYMBOL)
        if held is not None:
            stop_hit = (self.entry_close is not None
                        and close * BPS < self.entry_close * (BPS - STOP_BPS))
            recovered = ret > RECOVER_BPS
            dd_hit = dd_bps >= EXIT_DD_BPS
            if recovered or stop_hit or dd_hit:
                orders.append({"side": "sell", "symbol": SYMBOL, "qty": held.qty,
                               "max_slippage_bps": MAX_SLIPPAGE_BPS})
                self.entry_close = None
        else:
            self.entry_close = None
            near_end = state.seq >= 1440 - NO_ENTRY_LAST_BARS
            if ret < -DROP_BPS and dd_bps < HALT_DD_BPS and not near_end:
                room = state.weight_room(SYMBOL)
                amount = state.cash if state.cash < room else room
                if amount >= int(state.hello["fees"]["min_notional_quote_units"]):
                    orders.append({"side": "buy", "symbol": SYMBOL,
                                   "notional_quote_units": amount,
                                   "max_slippage_bps": MAX_SLIPPAGE_BPS})
                    self.entry_close = close
        return orders


BOT = Bot()


def on_batch(state, batch):
    return BOT.on_batch(state, batch)


makefun_bars.run(on_batch)
