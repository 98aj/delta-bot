"""EMA trend + pullback + reversal ("buy the dip in an up-trend, sell the rip in a down-trend").

1. Trend:    fast EMA above slow EMA = up-trend (below = down-trend). Optional ADX filter.
             Optionally the cross itself is also an entry (cross_entry), so the start of big moves is not missed.
2. Pullback: inside the trend, a bar's low reaches the pullback EMA (within pb_atr x ATR) while the close
             stays on the trend side of the slow EMA. Remembered for pb_window bars.
3. Reversal: a bar that closes back above the pullback EMA, above the previous bar's high, and green
             (mirror for shorts) -> enter at the next bar's open.
Stop:  below the lowest low of the last swing_n bars minus 0.25 ATR, kept between 1 and max_stop_atr ATR.
Exit:  trend flips (fast EMA crosses back); exit="slow" also exits when the close crosses the slow EMA.
       Optional ATR trailing stop (trail_atr; off by default, it cut returns in the sweep).
All rules use closed bars only, so the live signal equals the backtest signal.

BTC 2020-26 alone, 1% risk: ~19 trades/yr, CAGR 37%, max DD -8.9%, PF 2.67; PF 2.24 on 2024-26.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from deltabot.strategies.base import Strategy
from deltabot.strategies.indicators import adx


class EmaPullback(Strategy):
    name = "ema_pullback"
    min_bars = 260
    # chosen on BTC 2020-23 (scripts/pullback_research.py); every 4h EMA-50 variant tested was profitable on 2024-26
    defaults = {"tf": 4, "fast": 50, "slow": 100, "pb_ema": 50, "pb_atr": 0.0, "pb_window": 6, "adx_min": 20,
                "swing_n": 5, "max_stop_atr": 3.0, "trail_atr": 0.0, "cross_entry": True, "exit": "cross"}

    def __init__(self, **params):
        super().__init__(**params)
        self.bar_hours = int(self.p["tf"])

    def signals(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.p
        s = self.blank(df)
        a = s.atr
        c, h, l, o = df.close, df.high, df.low, df.open
        ef = c.ewm(span=p["fast"], adjust=False).mean()
        es = c.ewm(span=p["slow"], adjust=False).mean()
        ep = c.ewm(span=p["pb_ema"], adjust=False).mean()
        up, dn = ef > es, ef < es
        strong = adx(df) > p["adx_min"] if p["adx_min"] else pd.Series(True, df.index)

        pb_l = up & (l <= ep + p["pb_atr"] * a) & (c > es)
        pb_s = dn & (h >= ep - p["pb_atr"] * a) & (c < es)
        w = int(p["pb_window"])
        recent_l = pb_l.astype(int).rolling(w, min_periods=1).max().astype(bool)
        recent_s = pb_s.astype(int).rolling(w, min_periods=1).max().astype(bool)

        rev_l = up & strong & recent_l & (c > ep) & (c > h.shift()) & (c > o)
        rev_s = dn & strong & recent_s & (c < ep) & (c < l.shift()) & (c < o)
        if p["cross_entry"]:
            rev_l |= up & ~up.shift(fill_value=False) & strong
            rev_s |= dn & ~dn.shift(fill_value=False) & strong

        s.loc[rev_l, "side"] = 1
        s.loc[rev_s, "side"] = -1
        n = int(p["swing_n"])
        lo_stop = l.rolling(n).min() - 0.25 * a
        hi_stop = h.rolling(n).max() + 0.25 * a
        lo_stop = lo_stop.clip(lower=c - p["max_stop_atr"] * a, upper=c - a)
        hi_stop = hi_stop.clip(lower=c + a, upper=c + p["max_stop_atr"] * a)
        s["stop"] = np.where(s.side == 1, lo_stop, np.where(s.side == -1, hi_stop, np.nan))
        s["trail"] = p["trail_atr"] if p["trail_atr"] else np.nan
        if p["exit"] == "slow":        # looser: hold while price stays on the trend side of the slow EMA
            s["exit_long"] = dn | (c < es)
            s["exit_short"] = up | (c > es)
        else:
            s["exit_long"] = dn
            s["exit_short"] = up
        return s
