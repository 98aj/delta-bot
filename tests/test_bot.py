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
