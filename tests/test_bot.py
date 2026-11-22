"""End-to-end bot flow against a fake exchange: entry with exchange stop, end-of-day exit,
stop-loss detected via reconcile, and halting on an unexplained position."""
import pandas as pd
import pytest

from deltabot.bot import Bot
from deltabot.config import Settings, StrategyConf, SymbolConf
from deltabot.notify import Notifier
from deltabot.strategies import build

from conftest import FakeClient

HOUR = pd.Timedelta(hours=1)


def make_bot(tmp_path, client):
    s = Settings(env="testnet", api_key="k", api_secret="s", data_dir=tmp_path, log_dir=tmp_path,
                 history_days=120,
                 symbols=[SymbolConf("BTCUSD", [StrategyConf("williams_intraday", {})], max_contracts=5000)])
    bot = Bot(s, client=client, notifier=Notifier("testnet"), sleep=lambda _: None)
    bot.setup()
    return bot


def first_long(h1):
    sig = build("williams_intraday").signals(h1)
    longs = sig[(sig.side == 1) & (sig.index > h1.index[0] + pd.Timedelta(days=60))]
    return longs.index[0], float(sig.loc[longs.index[0], "stop"])


def at(t):  # 20 seconds after the 1h bar opened at t has closed
    return t + HOUR + pd.Timedelta(seconds=20)


def test_entry_then_end_of_day_exit(tmp_path, h1):
    t, stop = first_long(h1)
    fc = FakeClient(h1[h1.index <= t])
    fc.price = float(h1.close[t])
    bot = make_bot(tmp_path, fc)
    bot.tick(now=at(t))
    pos = bot.store.positions["BTCUSD"]
    assert pos.side == 1 and pos.size > 0 and fc.pos == pos.size
    assert pos.stop == pytest.approx(stop)
    stops = [o for o in fc.orders.values() if o["stop_price"] is not None]
    assert len(stops) == 1 and stops[0]["reduce_only"] and stops[0]["side"] == "sell"
    # risk ~1% of equity
    assert pos.risk_usd == pytest.approx(100, rel=0.05)

    eod = t.floor("D") + pd.Timedelta(hours=23)
    fc.h1 = h1[h1.index <= eod]
    fc.price = float(h1.close[eod])
    bot.tick(now=at(eod))
    assert "BTCUSD" not in bot.store.positions and fc.pos == 0 and not fc.orders
    tr = bot.journal.trades()
    assert len(tr) == 1 and tr.reason.iloc[0] == "end of day"


def test_strategy_size_multiplier_scales_risk(tmp_path, h1, monkeypatch):
    from deltabot.strategies.williams_intraday import WilliamsIntraday
    orig = WilliamsIntraday.signals

    def half(self, df):
        s = orig(self, df)
        s["size"] = 0.5
        return s
    monkeypatch.setattr(WilliamsIntraday, "signals", half)
    t, _ = first_long(h1)
    fc = FakeClient(h1[h1.index <= t])
    fc.price = float(h1.close[t])
    bot = make_bot(tmp_path, fc)
    bot.tick(now=at(t))
    assert bot.store.positions["BTCUSD"].risk_usd == pytest.approx(50, rel=0.05)    # half of 1%


def test_exchange_stop_detected(tmp_path, h1):
    t, stop = first_long(h1)
    fc = FakeClient(h1[h1.index <= t])
    fc.price = float(h1.close[t])
    bot = make_bot(tmp_path, fc)
    bot.tick(now=at(t))
    assert fc.pos > 0
    fc.trigger_stop(stop - 5)
    bot.tick(now=at(t) + pd.Timedelta(minutes=1))
    tr = bot.journal.trades()
    assert len(tr) == 1 and "stop-loss" in tr.reason.iloc[0]
    assert tr.exit_price.iloc[0] == pytest.approx(stop - 5)
    assert tr.r_multiple.iloc[0] < -0.9
    assert "BTCUSD" not in bot.store.positions


def test_stop_trigger_fallback(tmp_path, h1):
    from deltabot.exchange.rest import DeltaAPIError
    from deltabot.execution.executor import Executor
    fc = FakeClient(h1)
    orig = fc.place_order

    def picky(*a, stop_trigger_method=None, **k):
        if stop_trigger_method == "mark_price":
            raise DeltaAPIError(400, {"error": {"code": "invalid_trigger"}}, "/v2/orders")
        return orig(*a, stop_trigger_method=stop_trigger_method, **k)
    fc.place_order = picky
    ex = Executor(fc, "mark_price")
    oid = ex.place_stop(fc.info, 1, 1, 60_000)
    assert fc.orders[oid]["stop_price"] == 60_000 and ex.stop_trigger == "last_traded_price"


def test_account_wide_leverage_cap(tmp_path, h1):
    """An open ETH position using the whole 3x leverage budget blocks a new BTC entry; a smaller one shrinks it."""
    from deltabot.bot import SymbolRuntime
    from deltabot.config import SymbolConf
    from deltabot.exchange.rest import SymbolInfo
    from deltabot.portfolio.state import Position
    t, stop = first_long(h1)
    fc = FakeClient(h1[h1.index <= t]); fc.price = float(h1.close[t])
    bot = make_bot(tmp_path, fc)            # equity 10,000, max_leverage 3 (Settings default)
    eth = SymbolInfo("ETHUSD", 99, 0.01, 0.05)
    bot.symbols["ETHUSD"] = SymbolRuntime(SymbolConf("ETHUSD", []), eth, feed=None)
    strat = bot.symbols["BTCUSD"].strategies[0]
    close = float(h1.close[t])
    bot.store.positions["ETHUSD"] = Position("ETHUSD", "x", 1, 1500, 2000.0, "", 1900, 1900, 100)  # 30k = 3x
    bot._enter(bot.symbols["BTCUSD"], strat, 1, stop, close, at(t))
    assert fc.pos == 0                                  # no room left
    bot.store.positions["ETHUSD"].size = 1000           # 20k = 2x used -> 1x (10k) left for BTC
    bot._enter(bot.symbols["BTCUSD"], strat, 1, stop, close, at(t))
    assert 0 < fc.pos * 0.001 * close <= 10_000 + 1e-6


def test_halts_on_unknown_position(tmp_path, h1):
    fc = FakeClient(h1)
    fc.pos = 7   # someone traded manually
    bot = make_bot(tmp_path, fc)
    assert "BTCUSD" in bot.store.state.halted
    n = len(fc.placed)
    bot.tick(now=h1.index[-1] + HOUR + pd.Timedelta(seconds=20))
    assert len(fc.placed) == n      # no orders while halted


def test_stale_bar_not_traded_after_restart(tmp_path, h1):
    t, _ = first_long(h1)
    fc = FakeClient(h1[h1.index <= t])
    bot = make_bot(tmp_path, fc)
    bot.tick(now=at(t) + pd.Timedelta(minutes=30))   # bot started 30 min after the signal
    assert fc.pos == 0 and "BTCUSD" not in bot.store.positions


def test_state_survives_restart(tmp_path, h1):
    t, _ = first_long(h1)
    fc = FakeClient(h1[h1.index <= t])
    fc.price = float(h1.close[t])
    bot = make_bot(tmp_path, fc)
    bot.tick(now=at(t))
    bot2 = make_bot(tmp_path, fc)    # restart: same exchange, same files
    assert bot2.store.positions["BTCUSD"].size == fc.pos
    assert "BTCUSD" not in bot2.store.state.halted


class _FixedSignal:
    """Stand-in strategy that gives one fixed entry signal on every 1h bar (to drive the priority logic)."""
    bar_hours = 1
    min_bars = 1

    def __init__(self, name, side):
        self.name, self.side = name, side

    def bars(self, h1):
        return h1

    def signals(self, bars):
        from deltabot.strategies.base import Strategy
        s = Strategy.blank(bars)
        s["side"] = self.side
        s["stop"] = bars.close - self.side * 1000.0
        return s


def _held_by_williams(tmp_path, h1, preempt, order):
    """Bot holding a Williams long; strategies replaced by fixed-signal stubs in the given priority order."""
    t, stop = first_long(h1)
    fc = FakeClient(h1[h1.index <= t]); fc.price = float(h1.close[t])
    bot = make_bot(tmp_path, fc)
    bot.tick(now=at(t))
    assert bot.store.positions["BTCUSD"].strategy == "williams_intraday" and fc.pos > 0
    rt = bot.symbols["BTCUSD"]
    rt.conf.preempt = preempt
    rt.strategies = order
    nxt = t + HOUR
    fc.h1 = h1[h1.index <= nxt]; fc.price = float(h1.close[nxt])
    return bot, fc, rt, nxt


def test_higher_priority_signal_replaces_williams(tmp_path, h1):
    williams_stub = _FixedSignal("williams_intraday", 0)      # Williams itself: no exit, no new signal
    bot, fc, rt, nxt = _held_by_williams(tmp_path, h1, True, [_FixedSignal("supertrend_4h", -1), williams_stub])
    bot.on_bar(rt, fc.h1, at(nxt))
    pos = bot.store.positions["BTCUSD"]
    assert pos.strategy == "supertrend_4h" and pos.side == -1 and fc.pos == -pos.size
    tr = bot.journal.trades()
    assert len(tr) == 1 and tr.strategy.iloc[0] == "williams_intraday" and tr.reason.iloc[0] == "replaced by supertrend_4h"
    stops = [o for o in fc.orders.values() if o["stop_price"] is not None]
    assert len(stops) == 1 and stops[0]["side"] == "buy"     # only the new short's stop is left


def test_no_preempt_keeps_williams(tmp_path, h1):
    bot, fc, rt, nxt = _held_by_williams(tmp_path, h1, False,
                                         [_FixedSignal("supertrend_4h", -1), _FixedSignal("williams_intraday", 0)])
    before = fc.pos
    bot.on_bar(rt, fc.h1, at(nxt))
    assert bot.store.positions["BTCUSD"].strategy == "williams_intraday" and fc.pos == before
    assert bot.journal.trades().empty


def test_lower_priority_signal_never_replaces(tmp_path, h1):
    # Williams listed FIRST here, so the other strategy ranks below it and must not take over
    bot, fc, rt, nxt = _held_by_williams(tmp_path, h1, True,
                                         [_FixedSignal("williams_intraday", 0), _FixedSignal("tep_4h", -1)])
    before = fc.pos
    bot.on_bar(rt, fc.h1, at(nxt))
    assert bot.store.positions["BTCUSD"].strategy == "williams_intraday" and fc.pos == before
