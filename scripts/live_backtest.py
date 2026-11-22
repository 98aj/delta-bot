"""Capital growth with the LIVE settings (config/settings.yaml), one shared account, hourly mark-to-market equity.
Compares the bot as configured now with trend_rider. Usage: python scripts/live_backtest.py [start_equity]"""
import sys
import pandas as pd
from deltabot.backtest.live_sim import Leg, simulate, summary, yearly
from deltabot.backtest.run import load_h1
from deltabot.config import ROOT, load_settings, load_symbol

pd.set_option("display.width", 200)
eq0 = float(sys.argv[1]) if len(sys.argv) > 1 else 10_000.0
btc = load_h1(ROOT / "data/history/btc_1h.csv"); eth = load_h1(ROOT / "data/history/eth_1h.csv")
WILL = ("williams_intraday", {"k": 0.7, "trend_filter": True, "trend_sma_days": 20, "last_entry_hour": 20})
def now_cfg(sym):
    sc = load_symbol(ROOT / "config", sym)
    return [(s.name, s.params) for s in sc.strategies if s.enabled], sc.preempt
BOTS = {
    "BOT NOW (configs)": lambda sym: now_cfg(sym),
    "trend_rider b1.5": lambda sym: ([("trend_rider", {"boost": 1.5})], False),
}
try:
    s = load_settings()
    R = dict(risk_pct=s.risk_pct, max_leverage=s.max_leverage, min_contracts=s.min_contracts,
             max_trade_risk_pct=s.max_trade_risk_pct, daily_loss_limit_pct=s.daily_loss_limit_pct)
except Exception as e:          # settings loader may need a .env; fall back to the values in settings.yaml
    print("using settings.yaml defaults:", e)
    R = dict(risk_pct=0.02, max_leverage=5.0, min_contracts=1, max_trade_risk_pct=0.05, daily_loss_limit_pct=0.03)
print("live settings:", R, f"start ${eq0:,.0f}\n")
for label, (legs_fn, start) in {
        "BTC only, 2020-26": (lambda f: [Leg("BTCUSD", btc, *f("BTCUSD"))], "2020-03-01"),
        "BTC + ETH, one shared balance, 2024-26": (lambda f: [Leg("BTCUSD", btc, *f("BTCUSD")), Leg("ETHUSD", eth, *f("ETHUSD"))], "2024-06-01"),
}.items():
    print("=" * 100, "\n", label)
    for name, f in BOTS.items():
        t, eq = simulate(legs_fn(f), equity0=eq0, start=start, **R)
        print(f"\n  {name}: {summary(eq, t, eq0)}")
        print(yearly(eq, eq0).to_string().replace("\n", "\n    "))
        if t.contracts.max() >= 2000:
            print(f"    note: max_contracts cap (2000) was hit on {int((t.contracts >= 2000).sum())} trades")
