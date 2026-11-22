"""Sweep trend_rider on BTC 2020-23 (train) only; report unseen BTC 2024-26 and ETH for the top settings."""
import itertools, sys
import pandas as pd
from deltabot.backtest import engine as E
from deltabot.backtest.run import load_h1
from deltabot.strategies import build

btc = load_h1("data/history/btc_1h.csv"); eth = load_h1("data/history/eth_1h.csv")

def score(name, params, h1, start, end):
    s = build(name, params)
    bars = s.bars(h1); sig = s.signals(bars)
    sig.iloc[:s.min_bars, sig.columns.get_loc("side")] = 0
    m = (bars.index >= start) & (bars.index < end)
    bars, sig = bars[m], sig[m]
    t = E.run(bars, sig, s.bar_hours)
    st = E.stats(t, bars)
    if not st.get("trades"): return dict(cagr=0, dd=0, mar=0, n=0, pf=0)
    return dict(cagr=st["cagr_pct"], dd=st["max_dd_pct"], mar=round(st["cagr_pct"]/-min(st["max_dd_pct"],-1),2),
                n=st["trades"], pf=st["profit_factor"])

# warm-up: indicators computed on full history, trades only inside the window
TR = ("2020-03-01", "2024-01-01"); TE = ("2024-01-01", "2027-01-01"); ET = ("2024-06-01", "2027-01-01")
def tz(a): return tuple(pd.Timestamp(x, tz="UTC") for x in a)
TR, TE, ET = tz(TR), tz(TE), tz(ET)

base = {}
for k, (h, w) in {"btc_train": (btc, TR), "btc_test": (btc, TE), "eth": (eth, ET)}.items():
    base[k] = score("ema_pullback", {}, h, *w)
print("ema_pullback baseline:", base)

grid = dict(regime=[0, 200], brk=[0, 20], adx_min=[0, 20], adx_max=[0, 35, 40, 45],
            vol_max=[0.0, 0.66, 0.75, 0.85], cross_entry=[True, False])
rows = []
for vals in itertools.product(*grid.values()):
    p = dict(zip(grid, vals))
    r = score("trend_rider", p, btc, *TR)
    rows.append({**p, **{f"tr_{k}": v for k, v in r.items()}})
df = pd.DataFrame(rows).sort_values("tr_mar", ascending=False)
pd.set_option("display.width", 250)
top = df.head(20).copy()
for k, (h, w) in {"te": (btc, TE), "eth": (eth, ET)}.items():
    res = [score("trend_rider", {g: r[g] for g in grid}, h, *w) for _, r in top.iterrows()]
    for m in ("cagr", "dd", "mar", "n"):
        top[f"{k}_{m}"] = [x[m] for x in res]
print(top.to_string(index=False))
df.to_csv("data/research/rider_sweep.csv", index=False)
