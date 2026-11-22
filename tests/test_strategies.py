"""No look-ahead: the signal the live bot sees on the last closed bar must equal the backtest's signal
for that bar computed on the full history."""
import numpy as np
import pytest

from deltabot.strategies import REGISTRY, build

COLS = ["side", "stop", "exit_long", "exit_short", "size"]


@pytest.mark.parametrize("name", list(REGISTRY))
def test_last_bar_equals_full_history(h1, name):
    strat = build(name)
    bars = strat.bars(h1)
    full = strat.signals(bars)
    rng = np.random.default_rng(1)
    picks = list(rng.choice(np.arange(strat.min_bars, len(bars)), 40, replace=False))
    picks += list(np.flatnonzero(full.side.to_numpy() != 0)[-10:])   # make sure real signals are covered
    for i in picks:
        live = strat.signals(bars.iloc[: i + 1]).iloc[-1]
        ref = full.iloc[i]
        for c in COLS:
            a, b = live[c], ref[c]
            if isinstance(a, float) and np.isnan(a):
                assert np.isnan(b), (name, bars.index[i], c)
            else:
                assert a == pytest.approx(b), (name, bars.index[i], c, a, b)


def test_williams_rules(h1):
    strat = build("williams_intraday")
    s = strat.signals(h1)
    entries = s[s.side != 0]
    assert len(entries) > 10
    assert (entries.index.hour < 20).all()                         # no entries after 20:00 UTC
    assert entries.groupby(entries.index.floor("D")).size().max() == 1   # at most one entry per day
    assert s.exit_long[s.index.hour == 23].all()                   # flat at 24:00 UTC


def test_tep_range_and_signals(h1):
    from deltabot.strategies.indicators import trend_efficiency_pressure
    strat = build("tep_4h")
    bars = strat.bars(h1)
    x = trend_efficiency_pressure(bars, 20).dropna()
    assert x.between(-1, 1).all()
    sig = strat.signals(bars)
    longs, shorts = sig[sig.side == 1], sig[sig.side == -1]
    assert len(longs) and len(shorts)
    assert (longs.stop < bars.close[longs.index]).all() and (shorts.stop > bars.close[shorts.index]).all()
    assert (sig.trail == 3.0).all()


def test_trend_rider_sizing(h1):
    strat = build("trend_rider", {"boost": 1.9})
    bars = strat.bars(h1)
    sig = strat.signals(bars)
    sizes = set(np.round(sig["size"].unique(), 4))
    assert sizes <= {1.9, 0.95, 0.475}                      # full, cross (x0.5), weak (x0.25)
    # same entries, stops and exits as ema_pullback; only the size differs
    ref = build("ema_pullback").signals(bars)
    for c in ["side", "exit_long", "exit_short"]:
        assert (sig[c] == ref[c]).all(), c


def test_trend_rider_live_window_matches_backtest(h1):
    """The live bot only loads ~120 days of 1h candles; the size (ATR percentile) must not depend on older data."""
    strat = build("trend_rider")
    bars = strat.bars(h1)
    full = strat.signals(bars)
    live_bars = bars.iloc[-720:]                            # 120 days of 4h bars
    live = strat.signals(live_bars)
    tail = full.index[-50:]
    assert np.allclose(live.loc[tail, "size"], full.loc[tail, "size"])
