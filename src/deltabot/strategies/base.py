"""Strategy interface.

A strategy only describes WHAT to do; it never talks to the exchange. Given closed 1h bars it builds the
bars it trades on (1h or 4h) and returns a signal frame with one row per bar:

  side        +1 enter long / -1 enter short / 0 nothing   (decided at the bar's close)
  stop        stop-loss price for a new entry
  trail       ATR multiple for a trailing stop (NaN = no trailing)
  atr         ATR at the bar
  exit_long   close a long at this bar's close
  exit_short  close a short at this bar's close
  size        risk multiplier for a new entry (1.0 = the configured risk_pct; 0.5 = half of it)

The same frame drives the backtester and the live bot, so live behaviour matches the tested rules.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from deltabot.data.candles import complete_bars
from deltabot.strategies.indicators import atr


class Strategy:
    name = "base"
    bar_hours = 1          # timeframe the strategy trades on
    min_bars = 50          # bars needed before signals are trusted
    defaults: dict = {}

    def __init__(self, **params):
        unknown = set(params) - set(self.defaults)
        if unknown:
            raise ValueError(f"{self.name}: unknown params {sorted(unknown)}")
        self.p = {**self.defaults, **params}

    def bars(self, h1: pd.DataFrame) -> pd.DataFrame:
        return complete_bars(h1, self.bar_hours)

    def signals(self, df: pd.DataFrame) -> pd.DataFrame:
        raise NotImplementedError

    @staticmethod
    def blank(df: pd.DataFrame) -> pd.DataFrame:
        s = pd.DataFrame(index=df.index)
        s["side"] = 0
        s["stop"] = np.nan
        s["target"] = np.nan
        s["trail"] = np.nan
        s["atr"] = atr(df)
        s["exit_long"] = False
        s["exit_short"] = False
        s["size"] = 1.0
        return s

    def __repr__(self):
        return f"{self.name}({', '.join(f'{k}={v}' for k, v in self.p.items())})"
