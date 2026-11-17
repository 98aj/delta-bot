"""Turns decisions into Delta orders.

Entry  = market order, then a reduce-only stop-loss order that lives ON THE EXCHANGE
         (so the position stays protected if the bot, PC or internet goes down).
         If the stop cannot be placed, the position is closed immediately.
Trail  = place the new stop first, then cancel the old one (never unprotected).
Exit   = cancel the stop, then a reduce-only market order.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from deltabot.exchange.rest import DeltaAPIError, DeltaClient, SymbolInfo

log = logging.getLogger("deltabot.execution")


@dataclass
class Fill:
    order_id: int
    size: int
    price: float


def _side(side: int) -> str:
    return "buy" if side > 0 else "sell"


class Executor:
    def __init__(self, client: DeltaClient, stop_trigger: str = "last_traded_price", poll_interval: float = 0.5):
        self.c = client
        self.stop_trigger = stop_trigger
        self.poll = poll_interval

    def _wait_filled(self, order: dict, timeout: float = 10.0) -> dict:
        deadline = time.time() + timeout
        while True:
            unfilled = int(float(order.get("unfilled_size") or 0))
            if order.get("state") in ("closed", "cancelled") or unfilled == 0:
                return order
            if time.time() > deadline:
                return order
            time.sleep(self.poll)
            order = self.c.get_order(order["id"])

    def market(self, sym: SymbolInfo, side: int, size: int, reduce_only: bool = False,
               tag: str = "", ref_price: float | None = None) -> Fill:
        coid = f"db-{tag[:10]}-{int(time.time())}"
        order = self.c.place_order(sym.product_id, size, _side(side), "market_order",
                                   reduce_only=reduce_only, client_order_id=coid)
        order = self._wait_filled(order)
        filled = int(float(order.get("size") or size)) - int(float(order.get("unfilled_size") or 0))
        price = float(order.get("average_fill_price") or ref_price or 0)
        log.info("market %s %s x%d -> filled %d @ %.2f (order %s)", _side(side), sym.symbol, size, filled,
                 price, order.get("id"))
        return Fill(order_id=int(order["id"]), size=filled, price=price)

    def place_stop(self, sym: SymbolInfo, pos_side: int, size: int, stop: float) -> int:
        px = sym.round_price(stop, "down" if pos_side > 0 else "up")

        def send(trigger):
            return self.c.place_order(sym.product_id, size, _side(-pos_side), "market_order", reduce_only=True,
                                      stop_price=px, stop_trigger_method=trigger,
                                      client_order_id=f"db-stop-{int(time.time())}")
        try:
            order = send(self.stop_trigger)
        except DeltaAPIError as e:
            if self.stop_trigger == "last_traded_price":
                raise
            log.warning("stop with %s rejected (%s); retrying with last_traded_price", self.stop_trigger, e)
            self.stop_trigger = "last_traded_price"
            order = send(self.stop_trigger)
        log.info("stop-loss placed: %s x%d @ %.2f (order %s)", _side(-pos_side), size, px, order.get("id"))
        return int(order["id"])

    def cancel(self, sym: SymbolInfo, order_id: int | None) -> None:
        if not order_id:
            return
        try:
            self.c.cancel_order(order_id, sym.product_id)
        except DeltaAPIError as e:   # already filled / cancelled is fine
            log.info("cancel %s: %s", order_id, e)

    def enter(self, sym: SymbolInfo, side: int, size: int, stop: float, tag: str,
              ref_price: float) -> tuple[Fill, int] | None:
        """Returns (fill, stop_order_id) or None if nothing is left open."""
        fill = self.market(sym, side, size, tag=tag, ref_price=ref_price)
        if fill.size <= 0:
            log.error("entry order not filled: %s", fill)
            return None
        if (fill.price - stop) * side <= 0:
            log.error("fill %.2f already beyond stop %.2f: closing immediately", fill.price, stop)
            self.market(sym, -side, fill.size, reduce_only=True, tag="abort")
            return None
        try:
            stop_id = self.place_stop(sym, side, fill.size, stop)
        except Exception as e:  # noqa: BLE001 - any failure here means an unprotected position
            log.critical("could not place stop-loss (%s): closing position", e)
            self.market(sym, -side, fill.size, reduce_only=True, tag="nostop")
            return None
        return fill, stop_id

    def move_stop(self, sym: SymbolInfo, pos_side: int, size: int, old_id: int | None, new_stop: float) -> int:
        new_id = self.place_stop(sym, pos_side, size, new_stop)
        self.cancel(sym, old_id)
        return new_id

    def exit(self, sym: SymbolInfo, pos_side: int, size: int, stop_id: int | None, tag: str,
             ref_price: float | None = None) -> Fill:
        self.cancel(sym, stop_id)
        return self.market(sym, -pos_side, size, reduce_only=True, tag=tag, ref_price=ref_price)

    def last_close_price(self, sym: SymbolInfo, pos_side: int) -> float | None:
        """Price of the most recent fill that reduced the position (used when the exchange stop fired)."""
        try:
            for f in self.c.fills(sym.product_id, 20):
                if f.get("side") == _side(-pos_side):
                    return float(f["price"])
        except Exception as e:  # noqa: BLE001
            log.warning("could not read fills: %s", e)
        return None
