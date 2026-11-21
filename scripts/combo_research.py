"""Research sweep (BTC only): single indicators across settings and timeframes, then 2- and 3-indicator combos.

Uses the bot's own backtest engine (Delta fees, slippage, funding, next-bar fills, 1% risk).
Split: in-sample (IS) 2020-2023 is used to pick, out-of-sample (OOS) 2024 onwards is only used to check.

  .venv\\Scripts\\python.exe scripts\\combo_research.py            # both stages
Outputs CSVs to data/research/.
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
from deltabot.strategies.indicators import adx, atr, supertrend_direction, trend_efficiency_pressure  # noqa: E402

OUT = ROOT / "data" / "research"
SPLIT = pd.Timestamp("2024-01-01", tz="UTC")
TFS = [1, 2, 4, 6, 8, 12, 24]

# ---------- direction states (+1 / -1), all computed on the bar's close ----------


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def sgn(x):
    return pd.Series(np.where(x > 0, 1, -1), x.index)


def rsi(c, n):
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


TRIGGERS = {
    **{f"ema{f}/{s}": (lambda df, f=f, s=s: sgn(ema(df.close, f) - ema(df.close, s)))
       for f, s in [(5, 13), (9, 21), (12, 26), (20, 50), (50, 100)]},
    **{f"st{n},{m:g}": (lambda df, n=n, m=m: supertrend_direction(df, n, m).astype(int))
       for n, m in [(10, 2), (10, 3), (14, 3), (20, 4)]},
    **{f"macd{a}/{b}/{c}": (lambda df, a=a, b=b, c=c: sgn(
        (ema(df.close, a) - ema(df.close, b)) - ema(ema(df.close, a) - ema(df.close, b), c)))
       for a, b, c in [(12, 26, 9), (8, 21, 5)]},
    **{f"rsi{n}>50": (lambda df, n=n: sgn(rsi(df.close, n) - 50)) for n in (14, 21)},
}


def daily_ema_state(h1, n):
    d = complete_bars(h1, 24)
    st = sgn(d.close - ema(d.close, n))
    st.index = st.index + pd.Timedelta(days=1)   # known only after the daily bar closes
    return st


def make_filters(df, h1, tf):
    """Filter states on df's bars. Directional ones are +1/-1; 'both' ones are True/False for either side."""
    close_t = df.index + pd.Timedelta(hours=tf)
    dly = {n: daily_ema_state(h1, n) for n in (50, 200)}
    a = adx(df)
    vol_ok = df.volume > df.volume.rolling(20).mean()
    return {
        "ema200": ("dir", sgn(df.close - ema(df.close, 200))),
        "ema9/21": ("dir", sgn(ema(df.close, 9) - ema(df.close, 21))),
        "st20,4": ("dir", supertrend_direction(df, 20, 4).astype(int)),
        "macd": ("dir", TRIGGERS["macd12/26/9"](df)),
        "rsi>50": ("dir", TRIGGERS["rsi14>50"](df)),
        "tep>0": ("dir", sgn(trend_efficiency_pressure(df).fillna(0))),
        "D_ema50": ("dir", pd.Series(dly[50].reindex(close_t, method="ffill").fillna(0).to_numpy(), df.index)),
        "D_ema200": ("dir", pd.Series(dly[200].reindex(close_t, method="ffill").fillna(0).to_numpy(), df.index)),
        "adx>20": ("both", a > 20),
        "adx>25": ("both", a > 25),
        "vol>avg": ("both", vol_ok),
    }


# ---------- one backtest ----------

_BARS: dict = {}
_H1 = None


def _init(h1):
    global _H1
    _H1 = h1


def bars_for(tf):
    if tf not in _BARS:
        df = complete_bars(_H1, tf)
        _BARS[tf] = (df, make_filters(df, _H1, tf), {})
    return _BARS[tf]


def evaluate(job):
    tf, trig, filt, mode, trail, stop_atr = job
    df, filters, tcache = bars_for(tf)
    if trig not in tcache:
        tcache[trig] = TRIGGERS[trig](df)
    t = tcache[trig]
    longs_ok = pd.Series(True, df.index)
    shorts_ok = pd.Series(True, df.index)
    for f in filt:
        kind, v = filters[f]
        if kind == "dir":
            longs_ok &= v == 1
            shorts_ok &= v == -1
        else:
            longs_ok &= v
            shorts_ok &= v
    if mode == "cross":       # trigger flips, filters must already agree
        go_l = (t == 1) & (t.shift() == -1) & longs_ok
        go_s = (t == -1) & (t.shift() == 1) & shorts_ok
    else:                      # "align": enter the first bar where trigger AND all filters agree
        al, as_ = (t == 1) & longs_ok, (t == -1) & shorts_ok
        go_l = al & ~al.shift(fill_value=False)
        go_s = as_ & ~as_.shift(fill_value=False)
    s = pd.DataFrame(index=df.index)
    s["atr"] = atr(df)
    s["side"] = np.where(go_l, 1, np.where(go_s, -1, 0))
    s["stop"] = np.where(s.side == 1, df.close - stop_atr * s.atr, df.close + stop_atr * s.atr)
    s["target"] = np.nan
    s["trail"] = trail
    s["exit_long"] = t == -1
    s["exit_short"] = t == 1
    s.iloc[:210, s.columns.get_loc("side")] = 0   # warm-up
    tr = E.run(df, s, tf)
    row = dict(tf=f"{tf}h", trigger=trig, filters="+".join(filt) or "-", n_ind=1 + len(filt), mode=mode,
               trail=trail, stop_atr=stop_atr)
    if tr.empty:
        return row
    st = E.stats(tr, df)
    row.update(cagr=st["cagr_pct"], max_dd=st["max_dd_pct"], trades=len(tr),
               trades_yr=round(len(tr) / ((df.index[-1] - df.index[0]).days / 365.25), 1))
    for tag, part in (("is", tr[tr.entry_time < SPLIT]), ("oos", tr[tr.entry_time >= SPLIT])):
        w, lo = part.R[part.R > 0].sum(), -part.R[part.R <= 0].sum()
        row[f"{tag}_n"] = len(part)
        row[f"{tag}_pf"] = round(w / lo, 2) if lo > 0 else np.nan
        row[f"{tag}_R"] = round(part.R.sum(), 1)
    row["win%"] = round(100 * (tr.R > 0).mean(), 1)
    row["max_R"] = round(tr.R.max(), 1)
    row["avg_win_R"] = round(tr.R[tr.R > 0].mean(), 2)
    top = tr.R.sort_values(ascending=False)
    row["top10_share%"] = round(100 * top.head(10).sum() / tr.R.sum(), 0) if tr.R.sum() > 0 else np.nan
    return row


def run_jobs(jobs, h1, procs=None):
    jobs = sorted(jobs, key=lambda j: j[0])     # same timeframe together -> cache reuse per worker
    with Pool(procs, initializer=_init, initargs=(h1,)) as p:
        return pd.DataFrame(p.map(evaluate, jobs, chunksize=8))


def score(df, min_is=20, min_oos=8):
    ok = df[(df.is_n >= min_is) & (df.oos_n >= min_oos)].copy()
    return ok.sort_values("is_pf", ascending=False)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    h1 = load_h1(ROOT / "data" / "history" / "btc_1h.csv")
    h1 = h1[h1.index >= pd.Timestamp("2020-01-01", tz="UTC")]
    pd.set_option("display.width", 250, "display.max_columns", 40)
    cols = ["tf", "trigger", "filters", "mode", "trail", "trades_yr", "win%", "is_pf", "oos_pf", "is_R", "oos_R",
            "cagr", "max_dd", "max_R", "avg_win_R", "top10_share%"]

    # Stage 1: every trigger alone, every timeframe, trail / no trail
    jobs = [(tf, trg, (), "cross", tr, 2.0) for tf in TFS for trg in TRIGGERS for tr in (np.nan, 3.0)]
    s1 = run_jobs(jobs, h1)
    s1.to_csv(OUT / "stage1_single.csv", index=False)
    print(f"STAGE 1: {len(s1)} single-indicator runs")
    print(score(s1)[cols].head(30).to_string(index=False))
    print("\nEMA 9/21 alone by timeframe:")
    print(s1[s1.trigger == "ema9/21"][cols].to_string(index=False))

    # Stage 2: best 10 trigger/timeframe pairs (by IS PF, both OOS-checked) + EMA 9/21 on 4h, add 1-2 filters
    best = score(s1).drop_duplicates(["tf", "trigger"]).head(10)[["tf", "trigger"]].values.tolist()
    best = {(int(tf[:-1]), trg) for tf, trg in best} | {(4, "ema9/21"), (4, "st20,4"), (2, "ema9/21")}
    fnames = list(make_filters(complete_bars(h1.iloc[:5000], 4), h1.iloc[:5000], 4))
    combos = [c for k in (1, 2) for c in itertools.combinations(fnames, k)]
    jobs = [(tf, trg, c, mode, tr, 2.0) for tf, trg in best for c in combos
            if trg.split(",")[0] not in "+".join(c) for mode in ("cross", "align") for tr in (np.nan, 3.0)]
    s2 = run_jobs(jobs, h1)
    s2.to_csv(OUT / "stage2_combos.csv", index=False)
    print(f"\nSTAGE 2: {len(s2)} combo runs on {len(best)} trigger/timeframe pairs")
    print(score(s2)[cols].head(40).to_string(index=False))
    print("\nEMA 9/21 4h combos (top 20 by IS PF):")
    print(score(s2[(s2.trigger == "ema9/21") & (s2.tf == "4h")])[cols].head(20).to_string(index=False))


if __name__ == "__main__":
    main()
