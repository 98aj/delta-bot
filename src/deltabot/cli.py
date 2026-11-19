"""Command line: python -m deltabot <command>

  check        test connection (public data; account balance if keys are set)
  test-order   TESTNET ONLY: place a tiny far-away limit order and cancel it (proves keys + signing work)
  run          start the bot (testnet by default)
  status       balance, positions, open orders, halted symbols
  report       performance from the trade journal
  resume SYM   clear a halt after you fixed a position mismatch
  stop         stop the running bot; --flatten also cancels orders and closes positions
  backtest     run the strategies on historical candles (data/history/btc_1h.csv; --symbol ETHUSD for eth_1h.csv)
"""
from __future__ import annotations

import argparse
import logging
import sys

import pandas as pd

from deltabot.config import ROOT, load_settings
from deltabot.exchange.rest import DeltaClient
from deltabot.logutil import setup_logging


def _client(s):
    return DeltaClient(s.rest_url, s.api_key, s.api_secret)


def cmd_check(s, args) -> int:
    c = _client(s)
    print(f"Environment: {s.env}  ({s.rest_url})")
    off = c.sync_time()
    print(f"Clock offset vs Delta: {off:+.1f}s")
    for sc in s.symbols:
        info = c.product(sc.symbol)
        t = c.ticker(sc.symbol)
        now = pd.Timestamp.now(tz="UTC")
        bars = c.candles(sc.symbol, "1h", int((now - pd.Timedelta(days=3)).timestamp()), int(now.timestamp()))
        last = bars.index[-1] if len(bars) else "none"
        print(f"{sc.symbol}: product id {info.product_id}, contract {info.contract_value}, tick {info.tick_size}, "
              f"mark {t.get('mark_price')}, last 1h candles: {len(bars)} (latest {last})")
    if not s.has_keys:
        print("No API keys in .env: skipped account checks. Copy .env.example to .env and add your DEMO key.")
        return 0
    for b in c.balances():
        if float(b.get("balance") or 0) != 0:
            print(f"Wallet {b.get('asset_symbol')}: balance {b.get('balance')}, available {b.get('available_balance')}")
    for sc in s.symbols:
        info = c.product(sc.symbol)
        print(f"{sc.symbol} position: {c.position_size(info.product_id):+d} contracts, "
              f"open orders: {len(c.open_orders(info.product_id))}")
    print("Account access OK.")
    return 0


def cmd_test_order(s, args) -> int:
    if s.env != "testnet":
        print("test-order only runs on testnet.")
        return 1
    c = _client(s)
    c.sync_time()
    sym = s.symbols[0].symbol
    info = c.product(sym)
    mark = float(c.ticker(sym)["mark_price"])
    px = info.round_price(mark * 0.8, "down")
    o = c.place_order(info.product_id, 1, "buy", "limit_order", limit_price=px, post_only=True,
                      client_order_id="db-selftest")
    print(f"Placed test order {o['id']}: buy 1 {sym} @ {px} (20% below market, will not fill), state {o.get('state')}")
    c.cancel_order(o["id"], info.product_id)
    print("Cancelled it. Keys, signing and order permissions work.")
    return 0


def cmd_run(s, args) -> int:
    from deltabot.bot import Bot
    if not s.has_keys:
        print("Missing DELTA_API_KEY / DELTA_API_SECRET in .env")
        return 1
    if s.env == "live":
        if not (s.allow_live and args.live):
            print("Refusing to trade real money: needs safety.allow_live: true in settings.yaml AND --live.")
            return 1
        if input("You are about to trade REAL MONEY on Delta India. Type LIVE to continue: ").strip() != "LIVE":
            print("Aborted.")
            return 1
    (s.data_dir / "KILL").unlink(missing_ok=True)
    try:
        Bot(s).run()
    except KeyboardInterrupt:
        logging.getLogger("deltabot").info("stopped by user (positions and exchange stops left as they are)")
    return 0


def cmd_status(s, args) -> int:
    from deltabot.portfolio.state import StateStore
    c = _client(s)
    c.sync_time()
    st = StateStore(s.data_dir / f"state_{s.env}.json").state
    print(f"Environment: {s.env}")
    print(f"Equity: {c.equity(s.margin_asset):,.2f} {s.margin_asset}")
    for sc in s.symbols:
        info = c.product(sc.symbol)
        pos = st.positions.get(sc.symbol)
        print(f"{sc.symbol}: Delta position {c.position_size(info.product_id):+d} | bot: "
              + (f"{pos.strategy} {'LONG' if pos.side > 0 else 'SHORT'} {pos.size} @ {pos.entry_price:,.1f}, "
                 f"stop {pos.stop:,.1f}" if pos else "flat"))
        for o in c.open_orders(info.product_id):
            print(f"   open order {o['id']}: {o.get('side')} {o.get('size')} {o.get('order_type')} "
                  f"stop={o.get('stop_price')} limit={o.get('limit_price')} state={o.get('state')}")
    print(f"Halted: {st.halted or 'none'}")
    return 0


def cmd_report(s, args) -> int:
    from deltabot.portfolio.journal import Journal, summarize
    j = Journal(s.data_dir / f"trades_{s.env}.db")
    since = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=args.days)
    t = j.trades(since)
    print(f"{s.env} trades, last {args.days} days: {summarize(t)}")
    if not t.empty:
        for name, g in t.groupby("strategy"):
            print(f"  {name}: {summarize(g)}")
        cols = ["exit_time", "symbol", "strategy", "side", "size", "entry_price", "exit_price", "pnl_usd",
                "r_multiple", "reason"]
        print(t[cols].tail(20).round(2).to_string(index=False))
    return 0


def cmd_resume(s, args) -> int:
    from deltabot.portfolio.state import StateStore
    store = StateStore(s.data_dir / f"state_{s.env}.json")
    store.resume(args.symbol)
    print(f"{args.symbol} resumed. If Delta still disagrees with the bot it will halt again.")
    return 0


def cmd_stop(s, args) -> int:
    from deltabot.portfolio.state import StateStore
    s.data_dir.mkdir(parents=True, exist_ok=True)
    (s.data_dir / "KILL").write_text("stop", encoding="utf-8")
    print("KILL file written: the running bot stops within one poll.")
    if not args.flatten:
        return 0
    c = _client(s)
    c.sync_time()
    store = StateStore(s.data_dir / f"state_{s.env}.json")
    for sc in s.symbols:
        info = c.product(sc.symbol)
        c.cancel_all(info.product_id)
        size = c.position_size(info.product_id)
        if size:
            c.place_order(info.product_id, abs(size), "sell" if size > 0 else "buy", "market_order",
                          reduce_only=True, client_order_id="db-killswitch")
            print(f"{sc.symbol}: closed {size:+d} contracts")
        store.clear_position(sc.symbol)
    print("All orders cancelled and positions closed. (Closed trades are not written to the journal.)")
    return 0


def cmd_backtest(s, args) -> int:
    from deltabot.backtest.run import main as bt_main
    csv = args.csv or str(ROOT / "data" / "history" / f"{args.symbol[:3].lower()}_1h.csv")
    return bt_main(csv, args.start, args.symbol)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="deltabot", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    sub.add_parser("test-order")
    r = sub.add_parser("run")
    r.add_argument("--live", action="store_true", help="required (with allow_live) when DELTA_ENV=live")
    sub.add_parser("status")
    rp = sub.add_parser("report")
    rp.add_argument("--days", type=int, default=7)
    rs = sub.add_parser("resume")
    rs.add_argument("symbol")
    sp = sub.add_parser("stop")
    sp.add_argument("--flatten", action="store_true", help="also cancel all orders and close positions")
    bt = sub.add_parser("backtest")
    bt.add_argument("--symbol", default="BTCUSD", help="config/symbols/<SYMBOL>.yaml to test (BTCUSD, ETHUSD)")
    bt.add_argument("--csv", default=None, help="1h history CSV (default: data/history/<btc|eth>_1h.csv)")
    bt.add_argument("--start", default="2020-01-01")
    args = ap.parse_args(argv)

    s = load_settings()
    setup_logging(s.log_dir, s.env)
    handlers = {"check": cmd_check, "test-order": cmd_test_order, "run": cmd_run, "status": cmd_status,
                "report": cmd_report, "resume": cmd_resume, "stop": cmd_stop, "backtest": cmd_backtest}
    try:
        return handlers[args.cmd](s, args)
    except Exception as e:  # noqa: BLE001
        logging.getLogger("deltabot").error("%s failed: %s", args.cmd, e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
