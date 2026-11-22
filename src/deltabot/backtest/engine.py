"""Small, conservative event-driven backtester for one perpetual futures contract.

Rules:
- Signals are computed on bar i's CLOSE; market entries fill at bar i+1 OPEN (no look-ahead).
- Limit entries (for SMC) fill if a later bar trades through the limit price before expiry.
- Stop and target are checked with each bar's high/low. If both are hit in one bar, the STOP is assumed first.
- Gaps through the stop fill at the open (worse price).
- Costs: Delta India taker 0.05% / maker 0.02% + 18% GST, plus slippage on market fills,
  plus funding of 0.01% per 8h (longs pay, shorts receive), which is BTC's long-run average.
- Sizing: risk RISK_PCT of equity per trade (entry to stop), capped at MAX_LEV x equity notional.
"""
import numpy as np
import pandas as pd

from deltabot.config import MAKER_FEE as MAKER, TAKER_FEE as TAKER
SLIP = 0.0002          # 0.02% per market fill
FUND_PER_HOUR = 0.0001 / 8
RISK_PCT = 0.01
MAX_LEV = 3.0



def run(df, sig, hours_per_bar, equity0=10_000.0):
    """sig: DataFrame aligned to df with columns:
       side (+1/-1/0 entry signal at close), stop (absolute stop price),
       target (absolute or NaN), trail (ATR multiple or NaN), atr,
       exit_long / exit_short (bool, exit at next open),
       optional limit (limit entry price) and expiry (bars the limit stays valid)."""
    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    side = sig.side.to_numpy(); stop_a = sig.stop.to_numpy(); tgt_a = sig.target.to_numpy()
    trail_a = sig.trail.to_numpy(); atr_a = sig.atr.to_numpy()
    xl = sig.exit_long.to_numpy(); xs = sig.exit_short.to_numpy()
    size_a = sig["size"].to_numpy() if "size" in sig else np.ones(len(df))
    lim_a = sig["limit"].to_numpy() if "limit" in sig else np.full(len(df), np.nan)
    exp_a = sig["expiry"].to_numpy() if "expiry" in sig else np.zeros(len(df))
    idx = df.index

    eq = equity0
    pos = 0; entry = stp = tgt = trail = qty = 0.0; ebar = 0; efee = 0.0; risk_amt = 0.0
    pend = None          # (side, stop, target, trail, limit or None, expiry_bar, size multiplier)
    pend_exit = False
    trades = []

    def close_trade(i, px, fee_rate):
        nonlocal eq, pos
        gross = qty * (px - entry) * pos
        fees = efee + qty * px * fee_rate
        funding = qty * entry * FUND_PER_HOUR * hours_per_bar * (i - ebar + 1) * pos
        pnl = gross - fees - funding
        eq += pnl
        trades.append(dict(entry_time=idx[ebar], exit_time=idx[i], side=pos, entry=entry, exit=px,
                           pnl=pnl, R=pnl / risk_amt if risk_amt else 0.0, equity=eq))
        pos = 0

    for i in range(1, len(df)):
        # 1) pending market exit from previous close
        if pos and pend_exit:
            close_trade(i, o[i] * (1 - SLIP * pos), TAKER)
        pend_exit = False

        # 2) pending entry
        if not pos and pend is not None:
            s, sp, tp, tr, lim, exp, mult = pend
            filled = False
            if lim is None:
                px = o[i] * (1 + SLIP * s); fee = TAKER; filled = True
            elif i <= exp:
                if (s == 1 and l[i] <= lim) or (s == -1 and h[i] >= lim):
                    px = min(o[i], lim) if s == 1 else max(o[i], lim); fee = MAKER; filled = True
            else:
                pend = None
            if filled:
                dist = abs(px - sp)
                if dist > 0 and (sp - px) * s < 0:
                    risk_amt = eq * RISK_PCT * mult
                    qty = min(risk_amt / dist, eq * MAX_LEV / px)
                    risk_amt = qty * dist
                    pos, entry, stp, tgt, trail, ebar = s, px, sp, tp, tr, i
                    efee = qty * px * fee
                pend = None

        # 3) manage open position inside bar i
        if pos:
            hit_stop = (l[i] <= stp) if pos == 1 else (h[i] >= stp)
            hit_tgt = (not np.isnan(tgt)) and ((h[i] >= tgt) if pos == 1 else (l[i] <= tgt))
            if hit_stop:
                px = (min(o[i], stp) if pos == 1 else max(o[i], stp)) * (1 - SLIP * pos)
                close_trade(i, px, TAKER)
            elif hit_tgt:
                close_trade(i, tgt, MAKER)
            else:
                if not np.isnan(trail):
                    if pos == 1:
                        stp = max(stp, c[i] - trail * atr_a[i])
                    else:
                        stp = min(stp, c[i] + trail * atr_a[i])
                if (pos == 1 and xl[i]) or (pos == -1 and xs[i]):
                    pend_exit = True

        # 4) new signal at close of bar i
        if not pos and pend is None and side[i] != 0 and not np.isnan(stop_a[i]):
            lim = None if np.isnan(lim_a[i]) else lim_a[i]
            pend = (int(side[i]), stop_a[i], tgt_a[i], trail_a[i], lim, i + int(exp_a[i]), float(size_a[i]))

        if eq <= 0:
            break

    if pos:
        close_trade(len(df) - 1, c[-1], TAKER)
    return pd.DataFrame(trades)


def stats(trades, df, equity0=10_000.0, label=""):
    if trades.empty:
        return dict(strategy=label, trades=0)
    w = trades[trades.pnl > 0]; ls = trades[trades.pnl <= 0]
    eqc = pd.concat([pd.Series([equity0]), trades.equity]).to_numpy()
    peak = np.maximum.accumulate(eqc)
    years = (df.index[-1] - df.index[0]).days / 365.25
    final = eqc[-1]
    daily = trades.set_index("exit_time").pnl.resample("D").sum()
    daily_eq = equity0 + daily.cumsum()
    rets = daily_eq.pct_change().dropna()
    sharpe = rets.mean() / rets.std() * np.sqrt(365) if rets.std() > 0 else 0
    return dict(
        strategy=label,
        trades=len(trades),
        win_rate=round(100 * len(w) / len(trades), 1),
        avg_win_R=round(w.R.mean(), 2) if len(w) else 0,
        avg_loss_R=round(ls.R.mean(), 2) if len(ls) else 0,
        expectancy_R=round(trades.R.mean(), 3),
        profit_factor=round(w.pnl.sum() / -ls.pnl.sum(), 2) if len(ls) and ls.pnl.sum() < 0 else np.inf,
        total_return_pct=round(100 * (final / equity0 - 1), 1),
        cagr_pct=round(100 * ((final / equity0) ** (1 / years) - 1), 1) if final > 0 else -100,
        max_dd_pct=round(100 * ((eqc - peak) / peak).min(), 1),
        sharpe=round(sharpe, 2),
    )


def yearly(trades, equity0=10_000.0):
    if trades.empty:
        return pd.Series(dtype=float)
    t = trades.copy()
    t["year"] = t.exit_time.dt.year
    start = t.groupby("year").equity.first() - t.groupby("year").pnl.first()
    end = t.groupby("year").equity.last()
    return (100 * (end / start - 1)).round(1)
