"""Backtest the bot exactly as it runs live on one Delta account: several strategies, ONE net position per
symbol, strategies checked in priority order at each 1h close; the owner of the position manages it.

Fills: market at the next 1h open (+slippage, taker fee). Stops checked on every 1h bar (gaps fill at open).
Trailing stops and exit signals are evaluated when the owning strategy's bar closes. Funding charged hourly.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from deltabot.backtest import engine as E
from deltabot.strategies import build

HOUR = pd.Timedelta(hours=1)


def run_portfolio(h1: pd.DataFrame, strategies: list[tuple[str, dict]], risk_pct: float = 0.01,
                  max_lev: float = 3.0, equity0: float = 10_000.0, preempt: bool = False,
                  sizing=None) -> pd.DataFrame:
    """preempt=True: an entry signal from a higher-priority strategy closes a lower-priority strategy's
    position and takes over (the bot's live behaviour when preemption is enabled).
    sizing(eq, peak, equity0) -> (dollar_risk, max_lev): optional custom position-sizing rule
    (default: risk_pct of current equity, i.e. the lot grows as the account grows)."""
    strats = [build(n, p) for n, p in strategies]
    # signal row per strategy, keyed by the time its bar CLOSES (that is when the bot sees it)
    sigs = []
    for s in strats:
        bars = s.bars(h1)
        sg = s.signals(bars)
        sg.iloc[:s.min_bars, sg.columns.get_loc("side")] = 0     # live bot also waits for min_bars of history
        sg.index = sg.index + pd.Timedelta(hours=s.bar_hours)
        sg["close"] = bars.close.to_numpy()
        if "size" not in sg:
            sg["size"] = 1.0
        sigs.append(sg.to_dict("index"))
    o, h, l = h1.open.to_numpy(), h1.high.to_numpy(), h1.low.to_numpy()
    idx = h1.index
    eq = peak = equity0
    pos = None         # dict(owner, side, qty, entry, stop, ebar, risk)
    pending = None      # ("enter", owner, side, stop) or ("exit",)
    trades = []

    def close(i, px):
        nonlocal eq, pos, peak
        gross = pos["qty"] * (px - pos["entry"]) * pos["side"]
        fees = pos["fee_in"] + pos["qty"] * px * E.TAKER
        funding = pos["qty"] * pos["entry"] * E.FUND_PER_HOUR * (i - pos["ebar"] + 1) * pos["side"]
        pnl = gross - fees - funding
        eq += pnl
        peak = max(peak, eq)
        trades.append(dict(strategy=pos["owner"], side=pos["side"], entry_time=idx[pos["ebar"]], exit_time=idx[i],
                           entry=pos["entry"], exit=px, pnl=pnl, R=pnl / pos["risk"], equity=eq))
        pos = None

    for i in range(len(h1)):
        # 1) act on decisions made at the previous close, at this bar's open
        if pending is not None:
            if pending[0] == "exit" and pos:
                close(i, o[i] * (1 - E.SLIP * pos["side"]))
            if pending[0] == "enter" and pos is not None and pending[-1]:     # preemption: close, then enter
                close(i, o[i] * (1 - E.SLIP * pos["side"]))
            if pending[0] == "enter" and pos is None:
                _, owner, side, stop, trail, mult, _ = pending
                px = o[i] * (1 + E.SLIP * side)
                dist = abs(px - stop)
                if dist > 0 and (px - stop) * side > 0:
                    risk_usd, lev = sizing(eq, peak, equity0) if sizing else (eq * risk_pct, max_lev)
                    risk_usd *= mult
                    qty = min(risk_usd / dist, eq * lev / px)
                    pos = dict(owner=owner, side=side, qty=qty, entry=px, stop=stop, ebar=i, risk=qty * dist,
                               fee_in=qty * px * E.TAKER, trail=trail)
            pending = None
        # 2) exchange stop during this bar
        if pos:
            if (pos["side"] == 1 and l[i] <= pos["stop"]) or (pos["side"] == -1 and h[i] >= pos["stop"]):
                px = (min(o[i], pos["stop"]) if pos["side"] == 1 else max(o[i], pos["stop"])) * (1 - E.SLIP * pos["side"])
                close(i, px)
        # 3) decisions at this bar's close, strategies in priority order
        t_close = idx[i] + HOUR
        rank = {s.name: k for k, s in enumerate(strats)}
        for s, sg in zip(strats, sigs):
            row = sg.get(t_close)
            if row is None:
                continue
            if (preempt and pos and pending is None and rank[s.name] < rank[pos["owner"]] and row["side"] != 0
                    and row["stop"] == row["stop"]):
                pending = ("enter", s.name, int(row["side"]), float(row["stop"]),
                           float(row["trail"]) if row["trail"] == row["trail"] else None, float(row["size"]), True)
                break
            if pos and pos["owner"] == s.name:
                if (pos["side"] == 1 and row["exit_long"]) or (pos["side"] == -1 and row["exit_short"]):
                    pending = ("exit",)
                elif pos["trail"] == pos["trail"] and pos["trail"] is not None:
                    cand = row["close"] - pos["side"] * pos["trail"] * row["atr"]
                    if (cand - pos["stop"]) * pos["side"] > 0:
                        pos["stop"] = cand
            elif pos is None and pending is None and row["side"] != 0 and row["stop"] == row["stop"]:
                pending = ("enter", s.name, int(row["side"]), float(row["stop"]),
                           float(row["trail"]) if row["trail"] == row["trail"] else None, float(row["size"]), False)
        if eq <= 0:
            break
    if pos:
        close(len(h1) - 1, h1.close.iloc[-1])
    return pd.DataFrame(trades)


def summarize(trades: pd.DataFrame, h1: pd.DataFrame, equity0: float = 10_000.0) -> dict:
    eqc = np.concatenate([[equity0], trades.equity.to_numpy()])
    yrs = (h1.index[-1] - h1.index[0]).days / 365.25
    w, ls = trades[trades.pnl > 0], trades[trades.pnl <= 0]
    return dict(trades=len(trades), win_rate=round(100 * len(w) / len(trades), 1),
                profit_factor=round(w.pnl.sum() / -ls.pnl.sum(), 2),
                total_return_pct=round(100 * (eqc[-1] / equity0 - 1), 1),
                cagr_pct=round(100 * ((eqc[-1] / equity0) ** (1 / yrs) - 1), 1),
                max_dd_pct=round(100 * (eqc / np.maximum.accumulate(eqc) - 1).min(), 1))
