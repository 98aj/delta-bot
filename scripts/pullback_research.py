"""Sweep for ema_pullback on BTC. Pick on 2020-2023 (IS), check on 2024+ (OOS). Output: data/research/pullback_sweep.csv"""
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
from deltabot.strategies import build  # noqa: E402

SPLIT = pd.Timestamp("2024-01-01", tz="UTC")
_H1 = None
_BARS = {}


def _init(h1):
    global _H1
    _H1 = h1


def evaluate(params):
    st = build("ema_pullback", params)
    if st.bar_hours not in _BARS:
        _BARS[st.bar_hours] = st.bars(_H1)
    df = _BARS[st.bar_hours]
    sig = st.signals(df)
    sig.iloc[:st.min_bars, sig.columns.get_loc("side")] = 0
    tr = E.run(df, sig, st.bar_hours)
    row = dict(params)
    if tr.empty:
        return row
    s = E.stats(tr, df)
    row.update(trades_yr=round(len(tr) / ((df.index[-1] - df.index[0]).days / 365.25), 1), win=s["win_rate"],
               cagr=s["cagr_pct"], dd=s["max_dd_pct"], max_R=round(tr.R.max(), 1))
    for tag, part in (("is", tr[tr.entry_time < SPLIT]), ("oos", tr[tr.entry_time >= SPLIT])):
        w, lo = part.R[part.R > 0].sum(), -part.R[part.R <= 0].sum()
        row[f"{tag}_n"], row[f"{tag}_pf"], row[f"{tag}_R"] = len(part), (round(w / lo, 2) if lo else np.nan), round(part.R.sum(), 1)
    return row


def main():
    h1 = load_h1(ROOT / "data/history/btc_1h.csv")
    h1 = h1[h1.index >= pd.Timestamp("2020-01-01", tz="UTC")]
    grid = []
    for tf, (f, sl), pbsel, pb_atr, win, adx_min, trail, cross, ex in itertools.product(
            (2, 4, 6), ((9, 21), (20, 50), (21, 55), (50, 100), (50, 120)), ("fast", "slow"), (0.0, 0.5), (3, 6),
            (0, 20), (0.0, 3.0), (False, True), ("cross", "slow")):
        grid.append(dict(tf=tf, fast=f, slow=sl, pb_ema=f if pbsel == "fast" else sl, pb_atr=pb_atr, pb_window=win,
                         adx_min=adx_min, trail_atr=trail, cross_entry=cross, exit=ex))
    grid.sort(key=lambda g: g["tf"])
    with Pool(initializer=_init, initargs=(h1,)) as p:
        r = pd.DataFrame(p.map(evaluate, grid, chunksize=16))
    (ROOT / "data/research").mkdir(parents=True, exist_ok=True)
    r.to_csv(ROOT / "data/research/pullback_sweep.csv", index=False)
    pd.set_option("display.width", 250, "display.max_columns", 40)
    ok = r[(r.is_n >= 30) & (r.oos_n >= 10)]
    print(f"{len(r)} runs. Top 40 by IS profit factor (IS = 2020-23):")
    print(ok.sort_values("is_pf", ascending=False).head(40).to_string(index=False))
    print("\nTop 25 by IS total R:")
    print(ok.sort_values("is_R", ascending=False).head(25).to_string(index=False))
    print(f"\nshare of configs with OOS PF > 1: {(ok.oos_pf > 1).mean():.0%}")


if __name__ == "__main__":
    main()
