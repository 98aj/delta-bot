"""Larry Williams volatility breakout, intraday, on 1h bars (UTC day = 05:30 IST to 05:30 IST).

- Levels: today's open +/- k x yesterday's range.
- Enter on the FIRST 1h close beyond a level each day, only before last_entry_hour UTC.
- Trend filter: longs only if yesterday's daily close > its 20-day SMA, shorts only if below.
- Stop at today's open (idea failed). Flat at 24:00 UTC.
Backtest 2020-26 with Delta costs, k=0.5 + trend: PF ~1.10, ~2 trades/week.
"""
from __future__ import annotations

import pandas as pd

from deltabot.data.candles import resample
from deltabot.strategies.base import Strategy


def _day_map(df: pd.DataFrame, series: pd.Series) -> pd.Series:
    return pd.Series(series.reindex(df.index.floor("D")).to_numpy(), index=df.index)


def _first_of_day(mask: pd.Series) -> pd.Series:
    day = mask.index.floor("D")
    return mask & (mask.groupby(day).cumsum() == 1)


class WilliamsIntraday(Strategy):
    name = "williams_intraday"
    bar_hours = 1
    min_bars = 24 * 22
    defaults = {"k": 0.5, "trend_filter": True, "trend_sma_days": 20, "last_entry_hour": 20}

    def signals(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.p
        s = self.blank(df)
        daily = resample(df, "1D")
        o = _day_map(df, daily.open)
        r = _day_map(df, (daily.high - daily.low).shift())
        long_ = _first_of_day(df.close > o + p["k"] * r)
        short = _first_of_day(df.close < o - p["k"] * r)
        if p["trend_filter"]:
            up = (daily.close > daily.close.rolling(p["trend_sma_days"]).mean()).shift().astype(float)
            trend = _day_map(df, up)
            long_ &= trend == 1
            short &= trend == 0
        s.loc[long_, "side"] = 1
        s.loc[short & ~long_, "side"] = -1
        s["stop"] = o
        s.loc[df.index.hour >= p["last_entry_hour"], "side"] = 0
        closes_at_midnight = df.index.hour == 23          # 1h bar 23:00-24:00 UTC
        s["exit_long"] = closes_at_midnight
        s["exit_short"] = closes_at_midnight
        return s
