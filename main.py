"""cbBTC trend bot for make.fun btc-race-24h-20261117-0603.

Speaks agent-fun/bars-replay/v1: the harness sends hello once, then one
batch per 1-minute bar of cbBTC (CBBTC/USDC), then done. This bot answers
every batch with exactly one reply echoing its seq, carrying its orders
(buy/sell) for that bar; orders decided on a bar fill at the next bar's open.

Strategy: trend-following / momentum with a fee filter. A full-size round
trip costs ~26 bps (4 bps fee + 2 bps slippage + price impact per leg), so
the bot only trades when the expected move clearly beats that: it enters
long when the fast EMA is above the slow EMA and the trailing 60-bar return
is positive by a margin, and exits on a trend reversal, a trailing stop, or
a hard stop. Position sizing is one full-equity position at most (long only,
at most 1 open position), and a drawdown guard halts new entries well before
the 25% guardrail. Deterministic: floats only, no clock, no environment, no
unseeded randomness; the same bars give the same orders every run.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

SYMBOL = "CBBTC"

# --- Strategy parameters (tuned on the frozen dev window; see builder log) ---
FAST_SPAN = 12          # fast EMA span, in bars (minutes)
SLOW_SPAN = 48          # slow EMA span, in bars (minutes)
MOM_LOOKBACK = 60       # trailing-return lookback, in bars
MOM_MIN_BPS = 40        # entry needs trailing return above this (bps)
TRAIL_BPS = 60          # trailing stop: exit if close falls this far under the peak since entry
HARD_STOP_BPS = 100     # hard stop: exit if position value falls this far under its cost
TAKE_PROFIT_BPS = 0     # 0 = no take-profit exit (let winners run under the trailing stop)
COOLDOWN_BARS = 15      # bars to wait after an exit before a new entry
HALT_DD_BPS = 1000      # halt new entries once equity drawdown passes this (10%)
EXIT_DD_BPS = 1500      # exit the position once equity drawdown passes this (15%)
NO_ENTRY_LAST_BARS = 30  # do not open a position this close to the end of the window
MAX_SLIPPAGE_BPS = 100  # 1% past the close the order was decided on

BPS = 10_000


class Ema:
    """One EMA: the mean of its first span of closes, then 2/(span+1) steps."""

    def __init__(self, span):
        self.span = span
        self.warm = []
        self.value = None

    def step(self, close):
        if self.value is not None:
            self.value += (2.0 / (self.span + 1)) * (close - self.value)
            return
        self.warm.append(close)
        if len(self.warm) == self.span:
            self.value = sum(self.warm) / self.span


class Bot:
    def __init__(self):
        self.closes = []          # closes, oldest first
        self.fast = Ema(FAST_SPAN)
        self.slow = Ema(SLOW_SPAN)
        self.gap_prev = None      # fast - slow after the previous bar
        self.mom_prev = None      # trailing return (bps, float) after the previous bar
        self.peak_close = None    # highest close since the current entry
        self.peak_equity = None   # highest equity seen (for the drawdown guard)
        self.cooldown_until = -1  # seq before which no new entry
        self.last_seq = -1

    def _momentum_bps(self):
        """Trailing return over MOM_LOOKBACK bars, in bps (None if too few bars)."""
        if len(self.closes) <= MOM_LOOKBACK:
            return None
        then = self.closes[-1 - MOM_LOOKBACK]
        now = self.closes[-1]
        return (now - then) * BPS / then

    def on_batch(self, state, batch):
        orders = []
        self.last_seq = state.seq
        bar = None
        for b in batch["bars"]:
            if b["symbol"] == SYMBOL:
                bar = b
                break
        if bar is None:
            return orders  # no bar for our symbol this minute: nothing to decide on
        close = int(bar["c"])
        self.closes.append(close)
        self.fast.step(close)
        self.slow.step(close)

        # Drawdown guard runs on the account equity the batch carries.
        if self.peak_equity is None or state.equity > self.peak_equity:
            self.peak_equity = state.equity
        dd_bps = (self.peak_equity - state.equity) * BPS / self.peak_equity

        if state.warming_up:
            return orders
        if self.fast.value is None or self.slow.value is None:
            return orders
        gap = self.fast.value - self.slow.value
        mom = self._momentum_bps()

        held = state.positions.get(SYMBOL)
        if held is not None:
            if self.peak_close is None or close > self.peak_close:
                self.peak_close = close
            value = state.value(SYMBOL, held.qty)
            cross_down = self.gap_prev is not None and gap < 0 and self.gap_prev >= 0
            trail_hit = (close * BPS) < self.peak_close * (BPS - TRAIL_BPS)
            hard_hit = value * BPS < held.cost * (BPS - HARD_STOP_BPS)
            tp_hit = (TAKE_PROFIT_BPS > 0
                      and value * BPS >= held.cost * (BPS + TAKE_PROFIT_BPS))
            dd_hit = dd_bps >= EXIT_DD_BPS
            if cross_down or trail_hit or hard_hit or tp_hit or dd_hit:
                orders.append({"side": "sell", "symbol": SYMBOL, "qty": held.qty,
                               "max_slippage_bps": MAX_SLIPPAGE_BPS})
                self.cooldown_until = state.seq + COOLDOWN_BARS
                self.peak_close = None
        else:
            self.peak_close = None
            cross_up = self.gap_prev is not None and gap > 0 and self.gap_prev <= 0
            mom_cross = (mom is not None and self.mom_prev is not None
                         and mom > MOM_MIN_BPS and self.mom_prev <= MOM_MIN_BPS)
            trend_on = gap > 0 and mom is not None and mom > MOM_MIN_BPS
            late_entry = (state.seq - self.cooldown_until > COOLDOWN_BARS
                          and trend_on and self.mom_prev is not None
                          and self.mom_prev <= MOM_MIN_BPS)
            near_end = state.seq >= 1440 - NO_ENTRY_LAST_BARS
            if (cross_up or mom_cross or late_entry) and trend_on \
                    and state.seq >= self.cooldown_until \
                    and dd_bps < HALT_DD_BPS and not near_end:
                room = state.weight_room(SYMBOL)
                amount = state.cash if state.cash < room else room
                if amount >= int(state.hello["fees"]["min_notional_quote_units"]):
                    orders.append({"side": "buy", "symbol": SYMBOL,
                                   "notional_quote_units": amount,
                                   "max_slippage_bps": MAX_SLIPPAGE_BPS})
                    self.peak_close = close

        self.gap_prev = gap
        self.mom_prev = mom
        return orders


BOT = Bot()


def on_batch(state, batch):
    return BOT.on_batch(state, batch)


makefun_bars.run(on_batch)
