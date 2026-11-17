"""4h trend-following systems.

TrendEma4h: EMA 20/50 cross + ADX > 20 + EMA200 filter; 2 ATR initial stop, 3 ATR trailing stop,
            exit on the opposite cross. Backtest 2020-26: PF 1.85, max DD -6.7%, ~14 trades/yr.
Donchian4h: close beyond the 20-bar high/low; 2 ATR stop; exit on the 10-bar opposite channel.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from deltabot.strategies.base import Strategy
from deltabot.strategies.indicators import adx, supertrend_direction


class TrendEma4h(Strategy):
    name = "trend_ema_4h"
    bar_hours = 4
    min_bars = 260
    defaults = {"fast": 20, "slow": 50, "adx_min": 20, "stop_atr": 2.0, "trail_atr": 3.0}

    def signals(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.p
        s = self.blank(df)
        ef, es, e200 = (df.close.ewm(span=n, adjust=False).mean() for n in (p["fast"], p["slow"], 200))
        a = adx(df)
        up = (ef > es) & (ef.shift() <= es.shift())
        dn = (ef < es) & (ef.shift() >= es.shift())
        s.loc[up & (a > p["adx_min"]) & (df.close > e200), "side"] = 1
        s.loc[dn & (a > p["adx_min"]) & (df.close < e200), "side"] = -1
        s["stop"] = np.where(s.side == 1, df.close - p["stop_atr"] * s.atr, df.close + p["stop_atr"] * s.atr)
        s["trail"] = p["trail_atr"]
        s["exit_long"] = dn
        s["exit_short"] = up
        return s


class Supertrend4h(Strategy):
    """Enter when the Supertrend flips, exit on the opposite flip; 2 ATR protective stop.
    Robustness check 2020-26: all 30 settings (period 7-30, mult 2-5) profitable on BTC and ETH.
    20,4 on BTC: PF 1.98, ~14%/yr, max DD -14.5%, PF 2.24 on unseen 2025-26."""
    name = "supertrend_4h"
    bar_hours = 4
    min_bars = 100
    defaults = {"period": 20, "mult": 4.0, "stop_atr": 2.0}

    def signals(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.p
        s = self.blank(df)
        d = supertrend_direction(df, p["period"], p["mult"])
        up = (d == 1) & (d.shift() == -1)
        dn = (d == -1) & (d.shift() == 1)
        s.loc[up, "side"] = 1
        s.loc[dn, "side"] = -1
        s["stop"] = np.where(s.side == 1, df.close - p["stop_atr"] * s.atr, df.close + p["stop_atr"] * s.atr)
        s["exit_long"] = dn
        s["exit_short"] = up
        return s


class Donchian4h(Strategy):
    name = "donchian_4h"
    bar_hours = 4
    min_bars = 60
    defaults = {"entry_n": 20, "exit_n": 10, "stop_atr": 2.0}

    def signals(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.p
        s = self.blank(df)
        hi, lo = df.high.rolling(p["entry_n"]).max().shift(), df.low.rolling(p["entry_n"]).min().shift()
        xhi, xlo = df.high.rolling(p["exit_n"]).max().shift(), df.low.rolling(p["exit_n"]).min().shift()
        s.loc[df.close > hi, "side"] = 1
        s.loc[df.close < lo, "side"] = -1
        s["stop"] = np.where(s.side == 1, df.close - p["stop_atr"] * s.atr, df.close + p["stop_atr"] * s.atr)
        s["exit_long"] = df.close < xlo
        s["exit_short"] = df.close > xhi
        return s
