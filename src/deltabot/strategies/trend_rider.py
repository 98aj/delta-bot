"""trend_rider: ema_pullback's 4h EMA trend entries, sized by setup quality.

Built as a challenger to ema_pullback. Same entries, stops and exits by default; the edge is in SIZING:
full risk on pullback entries in normal conditions, half on EMA-cross entries, a quarter when the trend is
already stretched (ADX > 35) or volatility is in its top third. Research (scripts/rider_research.py) found
those weaker setups earn much less per R on BTC 2020-23, BTC 2024-26 and ETH alike; filtering them out
hurt (the next entry is often worse), sizing them down did not.
`boost` scales all entries so total risk can be raised back to the old drawdown level.

Optional extras, off by default because they did not survive out-of-sample tests:
1. Regime filter: longs only while the close is above a long EMA (regime), shorts only while below it.
   Cuts trend-cross trades that fire against the bigger trend.
2. Breakout re-entry: inside a trend, a close above the highest high of the last `brk` bars (below the lowest
   low for shorts) is also an entry. After a stop-out the trend often continues; ema_pullback has to wait for a
   new pullback and often misses it.
3. Exit: the opposite fast/slow EMA cross (as ema_pullback), plus an optional ATR chandelier trail that
   only starts tightening once the trade is well in profit (handled by the engine's `trail` column).
Stop: swing stop like ema_pullback (last swing_n bars +/- 0.25 ATR, kept 1..max_stop_atr ATR away).
All rules use closed bars only.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from deltabot.strategies.base import Strategy
from deltabot.strategies.indicators import adx


class TrendRider(Strategy):
    name = "trend_rider"
    min_bars = 260
    defaults = {"tf": 4, "fast": 50, "slow": 100, "regime": 0, "short_regime": True, "adx_min": 20,
                "pb_window": 6, "brk": 0, "swing_n": 5, "max_stop_atr": 3.0, "trail_atr": 0.0,
                "cross_entry": True, "shorts": True, "adx_max": 0, "vol_max": 0.0, "vol_n": 500,
                # quality sizing (risk multiplier per entry), chosen on BTC 2020-23 only
                "boost": 1.0, "size_cross": 0.5, "size_weak": 0.25, "weak_adx": 35, "weak_vol": 0.66,
                # extra exits (0 = off): chandelier (close beyond the chan_n-bar extreme -/+ chan_atr ATR), EMA close
                "chan_atr": 0.0, "chan_n": 60, "exit_ema": 0}

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
        up, dn = ef > es, ef < es
        ax = adx(df)
        strong = ax > p["adx_min"] if p["adx_min"] else pd.Series(True, df.index)
        if p["adx_max"]:                      # trend already stretched: entries there earned ~0R in research
            strong &= ax <= p["adx_max"]
        if p["vol_max"]:                      # skip entries in the top volatility band (ATR% percentile)
            vp = (a / c).rolling(int(p["vol_n"]), min_periods=250).rank(pct=True)
            strong &= ~(vp > p["vol_max"])
        if p["regime"]:
            er = c.ewm(span=p["regime"], adjust=False).mean()
            reg_l = c > er
            reg_s = (c < er) if p["short_regime"] else pd.Series(True, df.index)
        else:
            reg_l = reg_s = pd.Series(True, df.index)

        pb_l = up & (l <= ef) & (c > es)
        pb_s = dn & (h >= ef) & (c < es)
        w = int(p["pb_window"])
        recent_l = pb_l.astype(int).rolling(w, min_periods=1).max().astype(bool)
        recent_s = pb_s.astype(int).rolling(w, min_periods=1).max().astype(bool)
        go_l = recent_l & (c > ef) & (c > h.shift()) & (c > o)
        go_s = recent_s & (c < ef) & (c < l.shift()) & (c < o)
        if p["cross_entry"]:
            go_l |= up & ~up.shift(fill_value=False)
            go_s |= dn & ~dn.shift(fill_value=False)
        if p["brk"]:
            n = int(p["brk"])
            go_l |= up & (c > h.shift().rolling(n).max())
            go_s |= dn & (c < l.shift().rolling(n).min())
        go_l &= up & strong & reg_l
        go_s &= dn & strong & reg_s
        if not p["shorts"]:
            go_s &= False

        s.loc[go_l, "side"] = 1
        s.loc[go_s, "side"] = -1
        n = int(p["swing_n"])
        lo_stop = (l.rolling(n).min() - 0.25 * a).clip(lower=c - p["max_stop_atr"] * a, upper=c - a)
        hi_stop = (h.rolling(n).max() + 0.25 * a).clip(lower=c + a, upper=c + p["max_stop_atr"] * a)
        s["stop"] = np.where(s.side == 1, lo_stop, np.where(s.side == -1, hi_stop, np.nan))
        s["trail"] = p["trail_atr"] if p["trail_atr"] else np.nan
        s["exit_long"] = dn
        s["exit_short"] = up
        if p["chan_atr"]:                     # chandelier: give back at most chan_atr ATR from the recent extreme
            n = int(p["chan_n"])
            s["exit_long"] |= c < h.rolling(n).max() - p["chan_atr"] * a
            s["exit_short"] |= c > l.rolling(n).min() + p["chan_atr"] * a
        if p["exit_ema"]:                     # close on the wrong side of a slower EMA
            ex = c.ewm(span=p["exit_ema"], adjust=False).mean()
            s["exit_long"] |= c < ex
            s["exit_short"] |= c > ex

        # Quality sizing. Pullback entries in normal conditions get the full (boosted) risk.
        # EMA-cross entries get size_cross; entries with a stretched trend (ADX > weak_adx) or in the top
        # volatility band (ATR% percentile > weak_vol) get size_weak. In research these earned far less per R.
        cross = (up & ~up.shift(fill_value=False)) | (dn & ~dn.shift(fill_value=False))
        weak = pd.Series(False, df.index)
        if p["weak_adx"]:
            weak |= ax > p["weak_adx"]
        if p["weak_vol"]:
            vp = (a / c).rolling(int(p["vol_n"]), min_periods=250).rank(pct=True)
            weak |= vp > p["weak_vol"]
        mult = np.where(weak, p["size_weak"], np.where(cross, p["size_cross"], 1.0))
        s["size"] = p["boost"] * mult
        return s
