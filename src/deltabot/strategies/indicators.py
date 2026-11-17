"""Indicators, identical to the ones used in the backtests."""
import numpy as np
import pandas as pd


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df.close.shift()
    tr = pd.concat([df.high - df.low, (df.high - pc).abs(), (df.low - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def supertrend_direction(df: pd.DataFrame, n: int = 10, mult: float = 3.0) -> pd.Series:
    """+1 while price is above the Supertrend line, -1 while below (standard ATR band rules)."""
    a = atr(df, n).to_numpy()
    h, l, c = df.high.to_numpy(), df.low.to_numpy(), df.close.to_numpy()
    mid = (h + l) / 2
    ub, lb = mid + mult * a, mid - mult * a
    fub, flb = ub.copy(), lb.copy()
    d = np.ones(len(df))
    for i in range(1, len(df)):
        fub[i] = ub[i] if (ub[i] < fub[i - 1] or c[i - 1] > fub[i - 1]) else fub[i - 1]
        flb[i] = lb[i] if (lb[i] > flb[i - 1] or c[i - 1] < flb[i - 1]) else flb[i - 1]
        d[i] = 1 if c[i] > fub[i - 1] else (-1 if c[i] < flb[i - 1] else d[i - 1])
    return pd.Series(d, df.index)


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    up, dn = df.high.diff(), -df.low.diff()
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    ndm = np.where((dn > up) & (dn > 0), dn, 0.0)
    a = atr(df, n)
    pdi = 100 * pd.Series(pdm, df.index).ewm(alpha=1 / n, adjust=False).mean() / a
    ndi = 100 * pd.Series(ndm, df.index).ewm(alpha=1 / n, adjust=False).mean() / a
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi)
    return dx.ewm(alpha=1 / n, adjust=False).mean()
