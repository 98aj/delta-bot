"""No look-ahead: the signal the live bot sees on the last closed bar must equal the backtest's signal
for that bar computed on the full history."""
import numpy as np
import pytest

from deltabot.strategies import REGISTRY, build

COLS = ["side", "stop", "exit_long", "exit_short"]


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
