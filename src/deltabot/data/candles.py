"""Closed 1h candles from Delta, plus resampling to 4h / daily. Only fully closed bars are ever used."""
from __future__ import annotations

import logging

import pandas as pd

log = logging.getLogger("deltabot.data")
HOUR = pd.Timedelta(hours=1)


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    return df.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()


def complete_bars(h1: pd.DataFrame, hours: int) -> pd.DataFrame:
    """Resample 1h bars to `hours`-hour bars and keep only bars whose every hour is present and closed."""
    if hours == 1:
        return h1
    rule = f"{hours}h"
    out = resample(h1, rule)
    counts = h1.close.resample(rule, label="left", closed="left").count()
    return out[counts.reindex(out.index) == hours]


class CandleFeed:
    """Keeps a rolling window of closed 1h candles for one symbol."""

    def __init__(self, client, symbol: str, history_days: int = 120, settle_seconds: int = 15):
        self.client, self.symbol = client, symbol
        self.history_days, self.settle = history_days, pd.Timedelta(seconds=settle_seconds)
        self.bars = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    def update(self, now: pd.Timestamp | None = None) -> pd.DataFrame:
        now = now or pd.Timestamp.now(tz="UTC")
        end = int(now.timestamp())
        if self.bars.empty:
            start = int((now - pd.Timedelta(days=self.history_days)).timestamp())
        else:
            start = int((self.bars.index[-1] - 3 * HOUR).timestamp())
        new = self.client.candles(self.symbol, "1h", start, end)
        if not new.empty:
            self.bars = pd.concat([self.bars[~self.bars.index.isin(new.index)], new]).sort_index()
            cutoff = now - pd.Timedelta(days=self.history_days + 5)
            self.bars = self.bars[self.bars.index >= cutoff]
        return self.closed(now)

    def closed(self, now: pd.Timestamp) -> pd.DataFrame:
        """Bars whose close time (+ settle delay) has passed."""
        return self.bars[self.bars.index + HOUR + self.settle <= now]
