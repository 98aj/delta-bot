"""Backtest every registered strategy on 1h history (CSV: ts,open,high,low,close,volume), Delta costs included."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from deltabot.backtest import engine as E
from deltabot.strategies import REGISTRY


def load_h1(csv: str | Path) -> pd.DataFrame:
    df = pd.read_csv(csv, index_col=0, parse_dates=True)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df[["open", "high", "low", "close", "volume"]].astype(float)


def backtest(name: str, h1: pd.DataFrame, params: dict | None = None) -> tuple[pd.DataFrame, dict]:
    strat = REGISTRY[name](**(params or {}))
    bars = strat.bars(h1)
    trades = E.run(bars, strat.signals(bars), strat.bar_hours)
    st = E.stats(trades, bars, label=name)
    days = (bars.index[-1] - bars.index[0]).days
    st["trades_per_week"] = round(7 * len(trades) / days, 2) if days else 0
    return trades, st


def main(csv: str, start: str = "2020-01-01", symbol: str = "BTCUSD") -> int:
    if not Path(csv).exists():
        print(f"history file not found: {csv}")
        return 1
    h1 = load_h1(csv)
    h1 = h1[h1.index >= pd.Timestamp(start, tz="UTC")]
    print(f"1h bars: {len(h1)}  {h1.index[0].date()} -> {h1.index[-1].date()}  (Delta fees + slippage + funding)")
    rows = []
    for name in REGISTRY:
        trades, st = backtest(name, h1)
        rows.append(st)
        if len(trades):
            yr = E.yearly(trades)
            print(f"  {name} yearly %: " + ", ".join(f"{y}: {v:+.1f}" for y, v in yr.items()))
    cols = ["strategy", "trades", "trades_per_week", "win_rate", "avg_win_R", "avg_loss_R", "profit_factor",
            "total_return_pct", "cagr_pct", "max_dd_pct", "sharpe"]
    pd.set_option("display.width", 200)
    print(pd.DataFrame(rows)[cols].to_string(index=False))

    # the bot as configured: enabled strategies, one position at a time, priority order
    from deltabot.backtest.portfolio import run_portfolio, summarize
    from deltabot.config import ROOT, load_symbol
    sc = load_symbol(ROOT / "config", symbol)
    enabled = [(st.name, st.params) for st in sc.strategies if st.enabled]
    t = run_portfolio(h1, enabled, preempt=sc.preempt)
    print(f"\n{symbol} BOT AS CONFIGURED ({' > '.join(n for n, _ in enabled)}, "
          f"preempt={'on' if sc.preempt else 'off'}), one position at a time, 1% risk:")
    print(summarize(t, h1))
    by = t.groupby("strategy").agg(trades=("pnl", "size"), pnl=("pnl", "sum"), avg_R=("R", "mean")).round(2)
    print(by.to_string())
    yr = t.assign(y=t.exit_time.dt.year).groupby("y").apply(
        lambda g: round(100 * (g.equity.iloc[-1] / (g.equity.iloc[0] - g.pnl.iloc[0]) - 1), 1), include_groups=False)
    print("yearly %: " + ", ".join(f"{y}: {v:+.1f}" for y, v in yr.items()))
    return 0
