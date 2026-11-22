"""Can a different exit beat trend_rider? Live-settings sim (2% risk, whole contracts, hourly MTM drawdown).
Choose on BTC 2020-23; check BTC 2024-26 and BTC+ETH 2024-26 (shared balance) as unseen data."""
import itertools, sys
from concurrent.futures import ProcessPoolExecutor
import pandas as pd
from deltabot.backtest.live_sim import Leg, simulate, summary
from deltabot.backtest.run import load_h1

R = dict(risk_pct=0.02, max_leverage=5.0, min_contracts=1, max_trade_risk_pct=0.05, daily_loss_limit_pct=0.03)
btc = load_h1("data/history/btc_1h.csv"); eth = load_h1("data/history/eth_1h.csv")

def run(p, legs, start, end):
    b = btc[btc.index < end]; e = eth[eth.index < end]
    L = [Leg("BTCUSD", b, [("trend_rider", p)], False, 10**9)]
    if legs == 2:
        L.append(Leg("ETHUSD", e, [("trend_rider", p)], False, 10**9))
    t, eq = simulate(L, start=start, **R)
    s = summary(eq, t)
    return dict(cagr=s["cagr_pct"], dd=s["max_dd_pct"], mar=round(s["cagr_pct"] / -s["max_dd_pct"], 2), n=s["trades"])

def job(p):
    return p, run(p, 1, "2020-03-01", "2024-01-01")

def oos(p):
    return run(p, 1, "2024-01-01", "2027-01-01"), run(p, 2, "2024-06-01", "2027-01-01")

if __name__ == "__main__":
    base = {"boost": 1.5}
    cands = [base]
    cands += [{**base, "chan_atr": k, "chan_n": n} for k in (4, 6, 8, 10) for n in (30, 60, 120)]
    cands += [{**base, "exit_ema": e} for e in (100, 150, 200)]
    cands += [{**base, "trail_atr": k} for k in (5, 8, 12)]
    with ProcessPoolExecutor(6) as ex:
        res = list(ex.map(job, cands))
    rows = [{"params": str({k: v for k, v in p.items() if k != "boost"}), **{f"tr_{k}": v for k, v in r.items()}} for p, r in res]
    df = pd.DataFrame(rows); df["p"] = [p for p, _ in res]
    df = df.sort_values("tr_mar", ascending=False)
    top = df.head(6)
    if not (top.params == "{}").any():
        top = pd.concat([top, df[df.params == "{}"]])
    with ProcessPoolExecutor(6) as ex:
        o = list(ex.map(oos, top.p))
    for name, k in (("btc_test", 0), ("both", 1)):
        top[name] = [f"{x[k]['cagr']}%/{x[k]['dd']}% MAR {x[k]['mar']}" for x in o]
    pd.set_option("display.width", 250); pd.set_option("display.max_colwidth", 40)
    print(df.drop(columns="p").to_string(index=False))
    print("\nTOP on train, checked on unseen data:")
    print(top.drop(columns="p").to_string(index=False))
