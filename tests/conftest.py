from pathlib import Path

import pandas as pd
import pytest

from deltabot.backtest.run import load_h1
from deltabot.exchange.rest import SymbolInfo

HIST = Path(__file__).resolve().parents[1] / "data" / "history" / "btc_1h.csv"


@pytest.fixture(scope="session")
def h1() -> pd.DataFrame:
    if not HIST.exists():
        pytest.skip("data/history/btc_1h.csv not present")
    df = load_h1(HIST)
    return df[df.index >= "2025-06-01"]


class FakeClient:
    """In-memory stand-in for DeltaClient: serves historical candles and simulates instant market fills."""

    def __init__(self, h1: pd.DataFrame, equity: float = 10_000.0):
        self.h1, self._equity = h1, equity
        self.info = SymbolInfo("BTCUSD", 27, 0.001, 0.5)
        self.pos = 0
        self.price = float(h1.close.iloc[-1])
        self.orders: dict[int, dict] = {}
        self.fill_log: list[dict] = []
        self.next_id = 100
        self.placed: list[dict] = []

    # public
    def sync_time(self):
        return 0.0

    def product(self, symbol):
        return self.info

    def candles(self, symbol, resolution, start, end):
        idx = self.h1.index
        return self.h1[(idx >= pd.Timestamp(start, unit="s", tz="UTC")) & (idx < pd.Timestamp(end, unit="s", tz="UTC"))]

    # private
    def equity(self, asset="USD"):
        return self._equity

    def set_leverage(self, pid, lev):
        return {}

    def position_size(self, pid):
        return self.pos

    def place_order(self, product_id, size, side, order_type="market_order", limit_price=None, reduce_only=False,
                    post_only=False, client_order_id=None, stop_price=None, stop_trigger_method=None):
        self.next_id += 1
        o = {"id": self.next_id, "size": size, "side": side, "order_type": order_type, "stop_price": stop_price,
             "reduce_only": reduce_only}
        self.placed.append(o)
        if stop_price is not None:
            o["state"] = "pending"
            self.orders[o["id"]] = o
            return o
        sgn = 1 if side == "buy" else -1
        self.pos += sgn * size
        self.fill_log.insert(0, {"side": side, "price": self.price, "size": size})
        return {**o, "state": "closed", "unfilled_size": 0, "average_fill_price": str(self.price)}

    def get_order(self, oid):
        return self.orders[oid]

    def open_orders(self, pid):
        return list(self.orders.values())

    def cancel_order(self, oid, pid):
        self.orders.pop(oid, None)
        return {}

    def cancel_all(self, pid):
        self.orders.clear()

    def fills(self, pid, n=20):
        return self.fill_log[:n]

    # test helper: exchange-side stop fires
    def trigger_stop(self, price):
        for oid, o in list(self.orders.items()):
            self.pos = 0
            self.price = price
            self.fill_log.insert(0, {"side": o["side"], "price": price, "size": o["size"]})
            del self.orders[oid]
