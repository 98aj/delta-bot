"""Backtest that follows the LIVE bot's rules as closely as candles allow, for one shared account.

Differences from backtest/portfolio.py (which the README tables use):
- Sizing uses the live settings and code path: settings.yaml risk_pct (x the strategy's size multiplier),
  whole contracts via risk.sizing.contracts_for_risk, max_contracts, min_contracts, max_trade_risk_pct and an
  ACCOUNT-WIDE leverage cap (all symbols together within max_leverage x equity).
- Lot size is decided at the signal bar's close (like live), the fill comes at the next 1h open + slippage.
- All symbols trade one balance (BTC and ETH share equity, as on Delta).
- Daily loss limit: after losing daily_loss_limit_pct of equity in a UTC day, no new entries until 00:00 UTC.
- Equity is marked to market every hour, so max drawdown includes open-trade giveback, not just closed trades.
Costs: taker fee + GST on both legs, 0.02% slippage per market fill, funding 0.01%/8h (longs pay).
Not modelled: exchange outages, partial fills, liquidation (positions stay far below 10x margin), real funding.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from deltabot.backtest import engine as E
from deltabot.risk.sizing import contracts_for_risk
from deltabot.strategies import build

HOUR = pd.Timedelta(hours=1)
CONTRACT_VALUE = {"BTCUSD": 0.001, "ETHUSD": 0.01}


@dataclass
class Leg:
    symbol: str
    h1: pd.DataFrame
    strategies: list            # [(name, params)] in priority order
    preempt: bool = False
    max_contracts: int = 2000


def simulate(legs: list[Leg], risk_pct: float = 0.02, max_leverage: float = 5.0, min_contracts: int = 1,
             max_trade_risk_pct: float = 0.05, daily_loss_limit_pct: float = 0.03, equity0: float = 10_000.0,
             start: str | None = None):
    """Returns (trades DataFrame, hourly mark-to-market equity Series)."""
    start_ts = pd.Timestamp(start, tz="UTC") if start else None
    index = sorted(set().union(*[set(l.h1.index) for l in legs]))
    if start_ts is not None:
        index = [t for t in index if t >= start_ts]
    S = {}
    for leg in legs:
        strats = [build(n, p) for n, p in leg.strategies]
        sigs = []
        for s in strats:
            bars = s.bars(leg.h1)
            sg = s.signals(bars)
            sg.iloc[:s.min_bars, sg.columns.get_loc("side")] = 0
            sg.index = sg.index + pd.Timedelta(hours=s.bar_hours)      # keyed by the time the bar closes
            sg["close"] = bars.close.to_numpy()
            sigs.append(sg.to_dict("index"))
        S[leg.symbol] = dict(leg=leg, strats=strats, sigs=sigs, rank={s.name: k for k, s in enumerate(strats)},
                             bars=leg.h1.to_dict("index"), cv=CONTRACT_VALUE[leg.symbol], pos=None, pending=None,
                             last=None)

    bal = equity0               # realized wallet balance (what the live bot reads as equity)
    day, day_pnl = None, 0.0
    trades, curve = [], []

    def close(st, t, px, reason):
        nonlocal bal, day_pnl
        p = st["pos"]
        qty = p["n"] * st["cv"]
        gross = qty * (px - p["entry"]) * p["side"]
        fees = qty * (p["entry"] + px) * E.TAKER
        hours = (t - p["t"]) / HOUR + 1
        funding = qty * p["entry"] * E.FUND_PER_HOUR * hours * p["side"]
        pnl = gross - fees - funding
        bal += pnl
        day_pnl += pnl
        trades.append(dict(symbol=st["leg"].symbol, strategy=p["owner"], side=p["side"], contracts=p["n"],
                           entry_time=p["t"], exit_time=t, entry=p["entry"], exit=px, pnl=pnl,
                           R=pnl / p["risk"] if p["risk"] else 0.0, balance=bal, reason=reason))
        st["pos"] = None

    def notional_others(sym):
        return sum(o["pos"]["n"] * o["cv"] * o["pos"]["entry"] for k, o in S.items() if k != sym and o["pos"])

    for t in index:
        if t.floor("D") != day:
            day, day_pnl = t.floor("D"), 0.0
        # 1) orders decided at the previous close fill at this bar's open
        for sym, st in S.items():
            b = st["bars"].get(t)
            if b is None or st["pending"] is None:
                continue
            pd_ = st["pending"]; st["pending"] = None
            if pd_[0] == "exit" and st["pos"]:
                close(st, t, b["open"] * (1 - E.SLIP * st["pos"]["side"]), "exit signal")
                continue
            if pd_[0] == "enter":
                _, owner, side, stop, trail, n, preempt = pd_
                if preempt and st["pos"]:
                    close(st, t, b["open"] * (1 - E.SLIP * st["pos"]["side"]), f"replaced by {owner}")
                if st["pos"] is None and n > 0:
                    px = b["open"] * (1 + E.SLIP * side)
                    if (px - stop) * side > 0:
                        st["pos"] = dict(owner=owner, side=side, n=n, entry=px, stop=stop, t=t, trail=trail,
                                         risk=n * st["cv"] * abs(px - stop))
        # 2) exchange stop-loss inside this bar
        for sym, st in S.items():
            b, p = st["bars"].get(t), st["pos"]
            if b is None or p is None:
                continue
            if (p["side"] == 1 and b["low"] <= p["stop"]) or (p["side"] == -1 and b["high"] >= p["stop"]):
                fill = min(b["open"], p["stop"]) if p["side"] == 1 else max(b["open"], p["stop"])
                close(st, t, fill * (1 - E.SLIP * p["side"]), "stop-loss")
        # 3) decisions at this bar's close
        t_close = t + HOUR
        if t_close.floor("D") != day:          # the decision belongs to the next UTC day (limit resets)
            blocked = False
        else:
            blocked = day_pnl < -daily_loss_limit_pct * (bal - day_pnl)
        for sym, st in S.items():
            b = st["bars"].get(t)
            if b is None:
                continue
            st["last"] = b["close"]
            for s, sg in zip(st["strats"], st["sigs"]):
                row = sg.get(t_close)
                if row is None:
                    continue
                p = st["pos"]
                has_entry = row["side"] != 0 and row["stop"] == row["stop"]
                can_preempt = (st["leg"].preempt and p and st["pending"] is None and has_entry
                               and st["rank"][s.name] < st["rank"].get(p["owner"], 99))
                if p and p["owner"] == s.name and not can_preempt:
                    if (p["side"] == 1 and row["exit_long"]) or (p["side"] == -1 and row["exit_short"]):
                        st["pending"] = ("exit",)
                    elif p["trail"] == p["trail"] and p["trail"] is not None:
                        cand = row["close"] - p["side"] * p["trail"] * row["atr"]
                        if (cand - p["stop"]) * p["side"] > 0:
                            p["stop"] = cand
                    continue
                if not has_entry or st["pending"] is not None or (p and not can_preempt) or blocked:
                    continue
                side, stop, close_px = int(row["side"]), float(row["stop"]), float(row["close"])
                if (close_px - stop) * side <= 0:
                    continue
                mult = row.get("size", 1.0)
                mult = 1.0 if mult != mult else float(min(max(mult, 0.0), 2.0))
                lev_left = max(0.0, max_leverage - notional_others(sym) / bal) if bal > 0 else 0.0
                n = contracts_for_risk(bal, close_px, stop, st["cv"], risk_pct * mult, lev_left,
                                       st["leg"].max_contracts, min_contracts, max_trade_risk_pct, 0)
                if n < 1:
                    continue
                trail = float(row["trail"]) if row["trail"] == row["trail"] else None
                st["pending"] = ("enter", s.name, side, stop, trail, n, bool(p))
                break
        # 4) mark to market at this bar's close
        unreal = sum(st["pos"]["n"] * st["cv"] * (st["last"] - st["pos"]["entry"]) * st["pos"]["side"]
                     for st in S.values() if st["pos"] and st["last"] is not None)
        curve.append((t_close, bal + unreal))
        if bal <= 0:
            break
    for st in S.values():
        if st["pos"]:
            close(st, index[-1], st["last"], "end of data")
    eq = pd.Series(dict(curve))
    if len(eq):
        eq.iloc[-1] = bal
    return pd.DataFrame(trades), eq


def summary(eq: pd.Series, trades: pd.DataFrame, equity0: float = 10_000.0) -> dict:
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    dd = eq / eq.cummax() - 1
    closed = trades.balance.to_numpy() if len(trades) else np.array([equity0])
    cdd = (closed / np.maximum.accumulate(np.concatenate([[equity0], closed]))[1:] - 1).min() if len(trades) else 0
    w, ls = trades[trades.pnl > 0], trades[trades.pnl <= 0]
    return dict(start=round(equity0), end=round(eq.iloc[-1]), multiple=round(eq.iloc[-1] / equity0, 2),
                cagr_pct=round(100 * ((eq.iloc[-1] / equity0) ** (1 / yrs) - 1), 1),
                max_dd_pct=round(100 * dd.min(), 1), closed_trade_dd_pct=round(100 * cdd, 1),
                trades=len(trades), win_rate=round(100 * len(w) / max(len(trades), 1), 1),
                profit_factor=round(w.pnl.sum() / -ls.pnl.sum(), 2) if len(ls) else np.inf,
                worst_month_pct=round(100 * eq.resample("ME").last().pct_change().min(), 1))


def yearly(eq: pd.Series, equity0: float = 10_000.0) -> pd.DataFrame:
    ye = eq.resample("YE").last()
    prev = ye.shift(fill_value=equity0)
    dd = eq.groupby(eq.index.year).apply(lambda s: (s / s.cummax() - 1).min())
    return pd.DataFrame({"end_equity": ye.round(0).to_numpy(), "return_pct": (100 * (ye / prev - 1)).round(1).to_numpy(),
                         "max_dd_in_year_pct": (100 * dd).round(1).to_numpy()}, index=ye.index.year)
