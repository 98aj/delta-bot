"""trend_rider vs ema_pullback, through the same one-position portfolio backtest the README numbers use.
boost is calibrated on BTC 2020-23 only (match ema_pullback's training drawdown); BTC 2024-26 and ETH are unseen."""
import sys
import numpy as np
import pandas as pd
from deltabot.backtest.portfolio import run_portfolio
from deltabot.backtest.run import load_h1

btc = load_h1("data/history/btc_1h.csv"); eth = load_h1("data/history/eth_1h.csv")
eth = eth[eth.index >= "2024-01-01"]
W = 0.7
WILL = ("williams_intraday", {"k": 0.7, "trend_filter": True, "trend_sma_days": 20, "last_entry_hour": 20})

def window(t, h1, a, b):
    """CAGR / max DD of the trades exiting in [a, b), compounding from the equity at a."""
    a, b = pd.Timestamp(a, tz="UTC"), min(pd.Timestamp(b, tz="UTC"), h1.index[-1])
    t = t[(t.exit_time >= a) & (t.exit_time < b)]
    if t.empty: return dict(cagr=0, dd=0, mar=0, n=0)
    e0 = t.equity.iloc[0] - t.pnl.iloc[0]
    eqc = np.concatenate([[e0], t.equity.to_numpy()])
    yrs = (b - a).days / 365.25
    cagr = (eqc[-1] / e0) ** (1 / yrs) - 1
    dd = (eqc / np.maximum.accumulate(eqc) - 1).min()
    return dict(cagr=round(100 * cagr, 1), dd=round(100 * dd, 1), mar=round(cagr / -dd, 2), n=len(t))

def report(label, strategies, preempt_btc, preempt_eth):
    tb = run_portfolio(btc, strategies, preempt=preempt_btc)
    te = run_portfolio(eth, strategies, preempt=preempt_eth)
    rows = dict(btc_all=window(tb, btc, "2020-01-01", "2027-01-01"), btc_train=window(tb, btc, "2020-01-01", "2024-01-01"),
                btc_test=window(tb, btc, "2024-01-01", "2027-01-01"), eth=window(te, eth, "2024-01-01", "2027-01-01"))
    print(f"{label:48s} " + "  ".join(f"{k}: {v['cagr']:5.1f}%/{v['dd']:5.1f}% MAR {v['mar']:4.2f} n={v['n']}" for k, v in rows.items()))
    return rows

if __name__ == "__main__":
    report("ema_pullback alone", [("ema_pullback", {})], False, False)
    report("BOT NOW: ema_pullback > williams", [("ema_pullback", {}), WILL], True, False)
    for boost in [1.0, 1.25, 1.5, 1.75, 2.0]:
        report(f"trend_rider boost={boost}", [("trend_rider", {"boost": boost})], False, False)
