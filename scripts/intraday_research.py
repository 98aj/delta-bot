"""Intraday research (1h bars, flat by 24:00 UTC = 05:30 IST). Pick on BTC 2020-23 (IS), check on BTC 2024+ (OOS)
and ETH 2024+ (never used for picking). Uses the bot's backtest engine (Delta fees, slippage, funding, 1% risk).

Families:
  pullback  4h trend (EMA 50/100 on 4h) + 1h pullback to an EMA + reversal candle
  orb       opening-range breakout: range of the first n hours after a session start, trade the first break
  vwap      4h trend + price pulls back to the daily VWAP (from 00:00 UTC) and reverses
  williams  volatility breakout (today's open +/- k x yesterday's range), as the existing strategy, with filters
Every trade: stop on the other side of the setup (kept 0.5-3 ATR), optional ATR trailing stop, flat at 24:00 UTC,
no entries after last_entry UTC.
  .venv\\Scripts\\python.exe scripts\\intraday_research.py
"""
from __future__ import annotations

import itertools
import sys
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from deltabot.backtest import engine as E  # noqa: E402
from deltabot.backtest.run import load_h1  # noqa: E402
from deltabot.data.candles import complete_bars  # noqa: E402
from deltabot.strategies.indicators import adx, atr  # noqa: E402

SPLIT = pd.Timestamp("2024-01-01", tz="UTC")
WARM = 24 * 30


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def htf_trend(h1, tf=4, fast=50, slow=100):
    """+1/-1 trend of the higher timeframe, known at each 1h bar's close (no look-ahead)."""
    b = complete_bars(h1, tf)
    st = pd.Series(np.where(ema(b.close, fast) > ema(b.close, slow), 1, -1), b.index)
    st.index = st.index + pd.Timedelta(hours=tf)          # known when the higher bar closes
    known = st.reindex(h1.index + pd.Timedelta(hours=1), method="ffill").fillna(0).to_numpy()
    return pd.Series(known, h1.index)


def daily_trend(h1, n=20):
    d = complete_bars(h1, 24)
    st = pd.Series(np.where(d.close > d.close.rolling(n).mean(), 1, -1), d.index)
    st.index = st.index + pd.Timedelta(days=1)
    return pd.Series(st.reindex(h1.index + pd.Timedelta(hours=1), method="ffill").fillna(0).to_numpy(), h1.index)


def first_of_day(mask):
    day = mask.index.floor("D")
    return mask & (mask.astype(int).groupby(day).cumsum() == 1)


def build_signals(df, cache, p):
    c, h, l, o = df.close, df.high, df.low, df.open
    a = cache["atr"]
    hour = df.index.hour
    trend = cache["htf"] if p["trend"] == "4h" else cache["d20"] if p["trend"] == "d20" else pd.Series(0, df.index)
    use_trend = p["trend"] != "none"
    fam = p["family"]
    long_ = pd.Series(False, df.index)
    short = pd.Series(False, df.index)
    stop_l = l.rolling(5).min() - 0.1 * a
    stop_s = h.rolling(5).max() + 0.1 * a

    if fam == "pullback":
        e = ema(c, p["n"])
        pb_l = (l <= e) & (trend == 1)
        pb_s = (h >= e) & (trend == -1)
        rl = pb_l.astype(int).rolling(p["pbw"], min_periods=1).max().astype(bool)
        rs = pb_s.astype(int).rolling(p["pbw"], min_periods=1).max().astype(bool)
        long_ = (trend == 1) & rl & (c > e) & (c > h.shift()) & (c > o)
        short = (trend == -1) & rs & (c < e) & (c < l.shift()) & (c < o)
    elif fam == "vwap":
        day = df.index.floor("D")
        tp = (h + l + c) / 3
        vw = (tp * df.volume).groupby(day).cumsum() / df.volume.groupby(day).cumsum()
        pb_l = (l <= vw) & (trend == 1)
        pb_s = (h >= vw) & (trend == -1)
        rl = pb_l.astype(int).rolling(p["pbw"], min_periods=1).max().astype(bool)
        rs = pb_s.astype(int).rolling(p["pbw"], min_periods=1).max().astype(bool)
        long_ = (trend == 1) & rl & (c > vw) & (c > h.shift()) & (c > o)
        short = (trend == -1) & rs & (c < vw) & (c < l.shift()) & (c < o)
        long_ &= df.index.hour >= 2          # VWAP needs a few bars of the day
        short &= df.index.hour >= 2
    elif fam == "orb":
        start, n = p["start"], p["n"]
        rel = (hour - start) % 24
        day0 = (df.index - pd.Timedelta(hours=start)).floor("D")      # session day starting at `start` UTC
        in_rng = rel < n
        rh = h.where(in_rng).groupby(day0).transform("max")
        rlo = l.where(in_rng).groupby(day0).transform("min")
        after = rel >= n
        # only the first break of the session counts
        brk = after & ((c > rh) | (c < rlo))
        brk_first = brk & (brk.astype(int).groupby(day0).cumsum() == 1)
        long_ = brk_first & (c > rh)
        short = brk_first & (c < rlo)
        stop_l = (rlo + rh) / 2 if p["stop"] == "mid" else rlo
        stop_s = (rlo + rh) / 2 if p["stop"] == "mid" else rh
    elif fam == "williams":
        d = complete_bars(df, 24)
        do = pd.Series(d.open.reindex(df.index.floor("D")).to_numpy(), df.index)
        rng = pd.Series((d.high - d.low).shift().reindex(df.index.floor("D")).to_numpy(), df.index)
        lv_l, lv_s = do + p["k"] * rng, do - p["k"] * rng
        brk = (c > lv_l) | (c < lv_s)
        brk_first = brk & (brk.astype(int).groupby(df.index.floor("D")).cumsum() == 1)
        long_ = brk_first & (c > lv_l)
        short = brk_first & (c < lv_s)
        stop_l = stop_s = do

    if use_trend and fam in ("orb", "williams"):
        long_ &= trend == 1
        short &= trend == -1
    if p["adx"]:
        strong = cache["adx"] > p["adx"]
        long_ &= strong
        short &= strong
    ok_time = (hour < p["last_entry"]) & (hour >= p.get("first_entry", 0))
    long_ &= ok_time
    short &= ok_time
    # one entry per day
    any_ = long_ | short
    first = any_ & (any_.astype(int).groupby(df.index.floor("D")).cumsum() == 1)
    if p["one_per_day"]:
        long_ &= first
        short &= first

    s = pd.DataFrame(index=df.index)
    s["atr"] = a
    s["side"] = np.where(long_, 1, np.where(short, -1, 0))
    sl = pd.Series(stop_l, df.index).clip(lower=c - 3 * a, upper=c - 0.5 * a)
    ss = pd.Series(stop_s, df.index).clip(lower=c + 0.5 * a, upper=c + 3 * a)
    s["stop"] = np.where(s.side == 1, sl, np.where(s.side == -1, ss, np.nan))
    s["target"] = np.nan
    s["trail"] = p["trail"] if p["trail"] else np.nan
    flat = pd.Series(hour == 23, df.index)
    s["exit_long"] = flat | ((trend == -1) if (use_trend and p["exit_trend"]) else False)
    s["exit_short"] = flat | ((trend == 1) if (use_trend and p["exit_trend"]) else False)
    s.iloc[:WARM, s.columns.get_loc("side")] = 0
    return s


_D = {}


def _init(data):
    _D.update(data)


def _cache(sym):
    key = ("cache", sym)
    if key not in _D:
        h1 = _D[sym]
        _D[key] = dict(atr=atr(h1), adx=adx(h1), htf=htf_trend(h1), d20=daily_trend(h1))
    return _D[key]


def evaluate(job):
    sym, pid, p = job
    h1 = _D[sym]
    s = build_signals(h1, _cache(sym), p)
    tr = E.run(h1, s, 1)
    row = dict(sym=sym, id=pid, **p)
    if tr.empty:
        return row
    st = E.stats(tr, h1)
    yrs = (h1.index[-1] - h1.index[0]).days / 365.25
    row.update(trades_wk=round(len(tr) / yrs / 52, 2), win=st["win_rate"], cagr=st["cagr_pct"], dd=st["max_dd_pct"],
               avgR=round(tr.R.mean(), 3))
    parts = (("is", tr[tr.entry_time < SPLIT]), ("oos", tr[tr.entry_time >= SPLIT])) if sym == "BTC" else (("eth", tr),)
    for tag, part in parts:
        w, lo = part.R[part.R > 0].sum(), -part.R[part.R <= 0].sum()
        row[f"{tag}_n"], row[f"{tag}_pf"], row[f"{tag}_R"] = len(part), round(w / lo, 2) if lo else np.nan, round(part.R.sum(), 1)
    return row


def grid():
    base = dict(adx=0, trail=0.0, last_entry=20, one_per_day=True, exit_trend=False)
    out = []
    for n, win, tr, ax, tr2, opd, ext in itertools.product((20, 50), (3, 6), ("4h", "d20"), (0, 20), (0.0, 2.0),
                                                           (True, False), (False, True)):
        out.append({**base, "family": "pullback", "n": n, "pbw": win, "trend": tr, "adx": ax, "trail": tr2,
                    "one_per_day": opd, "exit_trend": ext})
    for win, tr, ax, tr2, opd in itertools.product((3, 6), ("4h", "d20"), (0, 20), (0.0, 2.0), (True, False)):
        out.append({**base, "family": "vwap", "n": 0, "pbw": win, "trend": tr, "adx": ax, "trail": tr2, "one_per_day": opd})
    for start, n, tr, stp, tr2, ax in itertools.product((0, 4, 8, 13), (1, 2, 4), ("none", "4h", "d20"), ("far", "mid"),
                                                         (0.0, 2.0), (0, 20)):
        out.append({**base, "family": "orb", "start": start, "n": n, "trend": tr, "stop": stp, "trail": tr2, "adx": ax})
    for k, tr, tr2, ax in itertools.product((0.3, 0.5, 0.7), ("none", "4h", "d20"), (0.0, 2.0), (0, 20)):
        out.append({**base, "family": "williams", "k": k, "trend": tr, "trail": tr2, "adx": ax})
    return out


def main():
    data = {}
    for sym, f, start in (("BTC", "btc", "2020-01-01"), ("ETH", "eth", "2024-01-01")):
        h1 = load_h1(ROOT / f"data/history/{f}_1h.csv")
        data[sym] = h1[h1.index >= pd.Timestamp(start, tz="UTC")]
    g = grid()
    with Pool(initializer=_init, initargs=(data,)) as pool:
        btc = pd.DataFrame(pool.map(evaluate, [("BTC", i, p) for i, p in enumerate(g)], chunksize=8))
    out = ROOT / "data/research"
    out.mkdir(parents=True, exist_ok=True)
    btc.to_csv(out / "intraday_btc.csv", index=False)
    pd.set_option("display.width", 260, "display.max_columns", 40)
    ok = btc[(btc.is_n >= 100) & (btc.oos_n >= 40)].sort_values("is_pf", ascending=False)
    print(f"{len(btc)} BTC runs; share with OOS PF > 1: {(ok.oos_pf > 1).mean():.0%}")
    print("by family (median IS PF / OOS PF / CAGR):")
    print(ok.groupby("family")[["is_pf", "oos_pf", "cagr"]].median().round(2).to_string())
    cols = ["id", "family", "n", "pbw", "start", "k", "trend", "stop", "adx", "trail", "one_per_day", "exit_trend",
            "trades_wk", "win", "is_pf", "oos_pf", "is_R", "oos_R", "cagr", "dd"]
    cols = [c for c in dict.fromkeys(cols) if c in ok]
    print("\nTop 30 by IS PF:")
    print(ok[cols].head(30).to_string(index=False))
    with Pool(initializer=_init, initargs=(data,)) as pool:
        eth = pd.DataFrame(pool.map(evaluate, [("ETH", i, g[i]) for i in ok.id.head(30)]))
    eth.to_csv(out / "intraday_eth_top.csv", index=False)
    print("\nSame top 30 on ETH 2024-26 (unseen):")
    ecols = [c for c in cols if c in eth and c not in ("is_pf", "oos_pf", "is_R", "oos_R")] + ["eth_n", "eth_pf", "eth_R"]
    print(eth[ecols].to_string(index=False))


if __name__ == "__main__":
    main()
