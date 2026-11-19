"""Main loop.

Every poll (30s):
  1. reconcile: compare the bot's recorded position with Delta's real position
     (detects stop-loss fills; halts the symbol on any mismatch it cannot explain)
  2. fetch candles; when a new 1h bar has closed, run each strategy on it:
     open position  -> exit signal? close.  trailing stop? move it.
     flat           -> entry signal? size by risk and enter with a stop on the exchange.
  3. once a day after 00:05 UTC: send the daily report.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from deltabot.config import ENDPOINTS, Settings, SymbolConf
from deltabot.data.candles import CandleFeed
from deltabot.exchange.rest import DeltaClient, SymbolInfo
from deltabot.execution.executor import Executor
from deltabot.notify import Notifier
from deltabot.portfolio.journal import Journal, summarize, trade_pnl
from deltabot.portfolio.state import Position, StateStore
from deltabot.risk.sizing import contracts_for_risk
from deltabot.strategies import Strategy, build

log = logging.getLogger("deltabot.bot")
HOUR = pd.Timedelta(hours=1)


@dataclass
class SymbolRuntime:
    conf: SymbolConf
    info: SymbolInfo
    feed: CandleFeed
    strategies: list[Strategy] = field(default_factory=list)


def _side_word(side: int) -> str:
    return "LONG" if side > 0 else "SHORT"


class Bot:
    def __init__(self, settings: Settings, client: DeltaClient | None = None, notifier: Notifier | None = None,
                 sleep=time.sleep):
        self.s = settings
        self.c = client or DeltaClient(settings.rest_url, settings.api_key, settings.api_secret)
        # candles may come from a different environment than orders (live data while paper trading)
        self.data_client = self.c if (client is not None or settings.market_data == settings.env) \
            else DeltaClient(ENDPOINTS[settings.market_data]["rest"])
        self.notify = notifier or Notifier(settings.env, settings.telegram_token, settings.telegram_chat_id)
        self.store = StateStore(settings.data_dir / f"state_{settings.env}.json")
        self.journal = Journal(settings.data_dir / f"trades_{settings.env}.db")
        self.ex = Executor(self.c, settings.stop_trigger)
        self.kill_file = settings.data_dir / "KILL"
        self.symbols: dict[str, SymbolRuntime] = {}
        self._sleep = sleep
        self._halt_notified: set[str] = set()

    # ------------------------------------------------------------------ startup
    def setup(self) -> None:
        self.c.sync_time()
        for sc in self.s.symbols:
            if not sc.enabled:
                continue
            info = self.c.product(sc.symbol)
            strategies = [build(st.name, st.params) for st in sc.strategies if st.enabled]
            feed = CandleFeed(self.data_client, sc.symbol, self.s.history_days, self.s.bar_settle_seconds)
            self.symbols[sc.symbol] = SymbolRuntime(sc, info, feed, strategies)
            try:
                self.c.set_leverage(info.product_id, self.s.exchange_leverage)
            except Exception as e:  # noqa: BLE001
                log.warning("%s: could not set exchange leverage: %s", sc.symbol, e)
            log.info("%s: product %d, contract %.4f, tick %s, strategies: %s", sc.symbol, info.product_id,
                     info.contract_value, info.tick_size, ", ".join(map(repr, strategies)))
        equity = self.equity()
        self.notify.send(f"Bot started | equity {equity:,.2f} {self.s.margin_asset} | "
                         f"{', '.join(self.symbols)} | risk {self.s.risk_pct:.1%}/trade | "
                         f"signals from {self.s.market_data} data, stops on {self.s.stop_trigger}")
        for rt in self.symbols.values():
            self.reconcile(rt)
            self._ensure_stop(rt)

    def equity(self) -> float:
        return self.c.equity(self.s.margin_asset)

    # ------------------------------------------------------------------ loop
    def run(self) -> None:
        self.setup()
        errors = 0
        while True:
            if self.kill_file.exists():
                self.notify.send("KILL file found: bot stopping.", logging.WARNING)
                return
            try:
                self.tick()
                errors = 0
            except KeyboardInterrupt:
                raise
            except Exception as e:  # noqa: BLE001 - keep running, but tell the user
                errors += 1
                log.exception("tick failed: %s", e)
                if errors in (1, 5, 20):
                    self.notify.send(f"⚠️ Error ({errors} in a row): {e}", logging.ERROR)
            self._sleep(self.s.poll_seconds)

    def tick(self, now: pd.Timestamp | None = None) -> None:
        now = now or pd.Timestamp.now(tz="UTC")
        for rt in self.symbols.values():
            if not self.reconcile(rt):
                continue
            h1 = rt.feed.update(now)
            if h1.empty:
                log.warning("%s: no candles received", rt.conf.symbol)
                continue
            last = h1.index[-1]
            prev = self.store.state.last_bar.get(rt.conf.symbol)
            if prev == last.isoformat():
                continue
            self.store.state.last_bar[rt.conf.symbol] = last.isoformat()
            self.store.save()
            age = now - (last + HOUR)
            if age > pd.Timedelta(minutes=self.s.max_signal_age_minutes):
                log.info("%s: bar %s closed %s ago, too old to act on (startup/restart)", rt.conf.symbol,
                         last, str(age).split(".")[0])
                continue
            self.on_bar(rt, h1, now)
        self._daily_report(now)

    # ------------------------------------------------------------------ reconcile
    def reconcile(self, rt: SymbolRuntime) -> bool:
        """True if the bot may trade this symbol now."""
        sym = rt.conf.symbol
        exch = self.c.position_size(rt.info.product_id)
        pos = self.store.positions.get(sym)
        expected = pos.side * pos.size if pos else 0
        if exch == expected:
            if sym in self.store.state.halted:
                return False
            return True
        if pos and exch == 0:
            price = self.ex.last_close_price(rt.info, pos.side) or pos.stop
            self._record_exit(rt, pos, price, "stop-loss hit on exchange")
            try:
                self.c.cancel_all(rt.info.product_id)
            except Exception as e:  # noqa: BLE001
                log.warning("cancel leftover orders failed: %s", e)
            return sym not in self.store.state.halted
        reason = f"position mismatch: Delta shows {exch:+d} contracts, bot expects {expected:+d}"
        if sym not in self.store.state.halted:
            self.store.halt(sym, reason)
        if sym not in self._halt_notified:
            self._halt_notified.add(sym)
            self.notify.send(f"🛑 {sym} halted: {reason}. Fix it on Delta (close the extra position), "
                             f"then run: deltabot resume {sym}", logging.ERROR)
        return False

    def _ensure_stop(self, rt: SymbolRuntime) -> None:
        pos = self.store.positions.get(rt.conf.symbol)
        if not pos:
            return
        ids = {int(o["id"]) for o in self.c.open_orders(rt.info.product_id)}
        if pos.stop_order_id not in ids:
            log.warning("%s: stop-loss order missing on Delta, placing it again @ %.2f", rt.conf.symbol, pos.stop)
            pos.stop_order_id = self.ex.place_stop(rt.info, pos.side, pos.size, pos.stop)
            self.store.set_position(pos)

    # ------------------------------------------------------------------ decisions
    def on_bar(self, rt: SymbolRuntime, h1: pd.DataFrame, now: pd.Timestamp) -> None:
        bar_close = h1.index[-1] + HOUR
        for strat in rt.strategies:
            bars = strat.bars(h1)
            if len(bars) < strat.min_bars:
                log.info("%s/%s: only %d bars, need %d (warming up)", rt.conf.symbol, strat.name, len(bars),
                         strat.min_bars)
                continue
            if bars.index[-1] + pd.Timedelta(hours=strat.bar_hours) != bar_close:
                continue   # e.g. 4h strategy between 4h closes
            sig = strat.signals(bars).iloc[-1]
            close = float(bars.close.iloc[-1])
            pos = self.store.positions.get(rt.conf.symbol)
            if pos and pos.strategy == strat.name:
                self._manage(rt, pos, sig, close)
            elif pos is None and sig.side != 0:
                self._enter(rt, strat, int(sig.side), float(sig.stop), close, now)
            elif pos is not None and sig.side != 0 and rt.conf.preempt and self._outranks(rt, strat, pos):
                self.notify.send(f"{rt.conf.symbol}: {strat.name} {_side_word(int(sig.side))} signal has priority, "
                                 f"closing the {pos.strategy} {_side_word(pos.side)} position first")
                fill = self.ex.exit(rt.info, pos.side, pos.size, pos.stop_order_id, tag="preempt", ref_price=close)
                if fill.size < pos.size:
                    self.notify.send(f"{rt.conf.symbol}: could not close {pos.strategy} position, "
                                     f"{strat.name} entry skipped", logging.WARNING)
                    continue
                self._record_exit(rt, pos, fill.price or close, f"replaced by {strat.name}")
                self._enter(rt, strat, int(sig.side), float(sig.stop), close, now)
            elif pos is not None and sig.side != 0:
                log.info("%s/%s: %s signal skipped, %s already holds the position", rt.conf.symbol, strat.name,
                         _side_word(int(sig.side)), pos.strategy)
            else:
                log.info("%s/%s: bar %s close %.1f, no signal", rt.conf.symbol, strat.name,
                         bars.index[-1].strftime("%m-%d %H:%M"), close)

    @staticmethod
    def _outranks(rt: SymbolRuntime, strat: Strategy, pos: Position) -> bool:
        """True if strat comes before the position's owner in the priority list (an owner that is no longer
        enabled ranks last)."""
        order = [s.name for s in rt.strategies]
        owner = order.index(pos.strategy) if pos.strategy in order else len(order)
        return order.index(strat.name) < owner

    def _entries_blocked(self, now: pd.Timestamp, equity: float) -> str | None:
        day_start = now.floor("D")
        realized = self.journal.realized_since(day_start)
        if realized < -self.s.daily_loss_limit_pct * (equity - realized):
            return f"daily loss limit reached ({realized:,.2f})"
        return None

    def _enter(self, rt: SymbolRuntime, strat: Strategy, side: int, stop: float, close: float,
               now: pd.Timestamp) -> None:
        sym = rt.conf.symbol
        if np.isnan(stop) or (close - stop) * side <= 0:
            log.info("%s/%s: signal ignored, stop %.2f on wrong side of %.2f", sym, strat.name, stop, close)
            return
        equity = self.equity()
        blocked = self._entries_blocked(now, equity)
        if blocked:
            self.notify.send(f"{sym}/{strat.name}: {_side_word(side)} signal skipped: {blocked}")
            return
        # account-wide cap: positions on ALL symbols together stay within max_leverage x equity
        used = sum(p.size * self.symbols[p.symbol].info.contract_value * p.entry_price
                   for p in self.store.positions.values() if p.symbol != sym and p.symbol in self.symbols)
        lev_left = max(0.0, self.s.max_leverage - used / equity) if equity else 0.0
        size = contracts_for_risk(equity, close, stop, rt.info.contract_value, self.s.risk_pct,
                                  lev_left, rt.conf.max_contracts, self.s.min_contracts,
                                  self.s.max_trade_risk_pct, rt.conf.fixed_contracts)
        risk_pct = size * abs(close - stop) * rt.info.contract_value / equity if equity else 0
        if size < 1:
            one = abs(close - stop) * rt.info.contract_value / equity if equity else 0
            if rt.info.contract_value * close > lev_left * equity:   # even 1 contract exceeds the leverage room
                why = f"account leverage cap reached ({used / equity:.1f}x used by other positions)"
            else:
                why = f"even 1 contract would risk {one:.1%} of equity (limit {self.s.max_trade_risk_pct:.0%})"
            self.notify.send(f"{sym}/{strat.name}: {_side_word(side)} signal skipped: {why}")
            return
        note = ""
        if rt.conf.fixed_contracts and size < rt.conf.fixed_contracts:
            note = f" (fixed lot {rt.conf.fixed_contracts} reduced by leverage/risk caps)"
        elif not rt.conf.fixed_contracts and risk_pct > self.s.risk_pct * 1.05:
            note = " (minimum lot, above target)"
        self.notify.send(f"SIGNAL {strat.name} {_side_word(side)} {sym} @ ~{close:,.1f} | stop {stop:,.1f} | "
                         f"{size} contracts | risk {risk_pct:.1%}{note}")
        res = self.ex.enter(rt.info, side, size, stop, tag=strat.name, ref_price=close)
        if res is None:
            self.notify.send(f"{sym}: entry aborted (see log)", logging.WARNING)
            return
        fill, stop_id = res
        risk = abs(fill.price - stop) * fill.size * rt.info.contract_value
        pos = Position(symbol=sym, strategy=strat.name, side=side, size=fill.size, entry_price=fill.price,
                       entry_time=now.isoformat(), stop=stop, initial_stop=stop, risk_usd=risk,
                       stop_order_id=stop_id)
        self.store.set_position(pos)
        self.notify.send(f"✅ OPENED {_side_word(side)} {sym} {fill.size} @ {fill.price:,.1f} | "
                         f"stop {stop:,.1f} on Delta | risk ${risk:,.2f}")

    def _manage(self, rt: SymbolRuntime, pos: Position, sig: pd.Series, close: float) -> None:
        exit_flag = bool(sig.exit_long) if pos.side > 0 else bool(sig.exit_short)
        if exit_flag:
            reason = "end of day" if pos.strategy == "williams_intraday" else "exit signal"
            fill = self.ex.exit(rt.info, pos.side, pos.size, pos.stop_order_id, tag="exit", ref_price=close)
            self._record_exit(rt, pos, fill.price or close, reason)
            return
        trail = sig.trail
        if trail is not None and not np.isnan(trail):
            cand = close - pos.side * trail * float(sig.atr)
            better = (cand - pos.stop) * pos.side > rt.info.tick_size
            if better and (close - cand) * pos.side > 0:
                pos.stop_order_id = self.ex.move_stop(rt.info, pos.side, pos.size, pos.stop_order_id, cand)
                old, pos.stop = pos.stop, cand
                self.store.set_position(pos)
                log.info("%s: trailing stop %.1f -> %.1f", rt.conf.symbol, old, cand)
                return
        log.info("%s/%s: holding %s %d @ %.1f, stop %.1f, close %.1f", rt.conf.symbol, pos.strategy,
                 _side_word(pos.side), pos.size, pos.entry_price, pos.stop, close)

    def _record_exit(self, rt: SymbolRuntime, pos: Position, price: float, reason: str) -> None:
        pnl, fees = trade_pnl(pos.side, pos.size, rt.info.contract_value, pos.entry_price, price)
        r = pnl / pos.risk_usd if pos.risk_usd else 0.0
        self.journal.add(symbol=pos.symbol, strategy=pos.strategy, side=pos.side, size=pos.size,
                         entry_time=pos.entry_time, entry_price=pos.entry_price,
                         exit_time=pd.Timestamp.now(tz="UTC").isoformat(), exit_price=price,
                         initial_stop=pos.initial_stop, risk_usd=pos.risk_usd, fees_usd=fees, pnl_usd=pnl,
                         r_multiple=r, reason=reason, env=self.s.env)
        self.store.clear_position(pos.symbol)
        icon = "🟢" if pnl > 0 else "🔴"
        self.notify.send(f"{icon} CLOSED {_side_word(pos.side)} {pos.symbol} ({pos.strategy}) @ {price:,.1f} | "
                         f"{reason} | P&L {pnl:+,.2f} ({r:+.2f}R, fees {fees:,.2f})")

    # ------------------------------------------------------------------ reporting
    def _daily_report(self, now: pd.Timestamp) -> None:
        day = now.floor("D")
        key = day.date().isoformat()
        if self.store.state.last_report_day == key or now - day < pd.Timedelta(minutes=5):
            return
        if not self.store.state.last_report_day:   # first run: don't report, just start counting
            self.store.state.last_report_day = key
            self.store.save()
            return
        self.store.state.last_report_day = key
        self.store.save()
        self.notify.send(self.report_text(day - pd.Timedelta(days=1), day))

    def report_text(self, start: pd.Timestamp, end: pd.Timestamp) -> str:
        t = self.journal.trades(start)
        t = t[t.exit_time < end] if not t.empty else t
        week = summarize(self.journal.trades(end - pd.Timedelta(days=7)))
        day = summarize(t)
        open_pos = [f"{p.symbol} {p.strategy} {_side_word(p.side)} {p.size} @ {p.entry_price:,.1f}, "
                    f"stop {p.stop:,.1f}" for p in self.store.positions.values()]
        try:
            eq = f"{self.equity():,.2f} {self.s.margin_asset}"
        except Exception:  # noqa: BLE001
            eq = "n/a"
        return (f"📊 Daily report {start.date()} ({self.s.env.upper()})\n"
                f"Trades: {day.get('trades', 0)} | P&L: {day.get('pnl_usd', 0):+,.2f} | R: {day.get('total_R', 0):+}\n"
                f"Last 7 days: {week.get('trades', 0)} trades, {week.get('total_R', 0):+} R, "
                f"win rate {week.get('win_rate_pct', 0)}%\n"
                f"Open: {'; '.join(open_pos) or 'none'}\n"
                f"Equity: {eq} | Halted: {', '.join(self.store.state.halted) or 'none'}")
