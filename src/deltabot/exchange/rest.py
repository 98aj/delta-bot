"""Delta Exchange India REST client (v2 API).

Private requests are signed with HMAC-SHA256 over: METHOD + timestamp + path + query_string + body,
and sent with the headers api-key, timestamp, signature. The secret itself is never sent.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode

import pandas as pd
import requests

log = logging.getLogger("deltabot.exchange")


class DeltaAPIError(Exception):
    def __init__(self, status: int, payload, path: str = ""):
        self.status, self.payload, self.path = status, payload, path
        err = payload.get("error") if isinstance(payload, dict) else payload
        super().__init__(f"Delta API {status} on {path}: {err}")

    @property
    def code(self) -> str:
        if isinstance(self.payload, dict) and isinstance(self.payload.get("error"), dict):
            return str(self.payload["error"].get("code", ""))
        return ""


@dataclass(frozen=True)
class SymbolInfo:
    symbol: str
    product_id: int
    contract_value: float   # underlying per contract, e.g. 0.001 BTC
    tick_size: float

    def round_price(self, price: float, direction: str = "nearest") -> float:
        q = price / self.tick_size
        q = {"down": math.floor, "up": math.ceil}.get(direction, round)(q)
        return round(q * self.tick_size, 8)


def sign(secret: str, method: str, timestamp: str, path: str, query: str, body: str) -> str:
    msg = method + timestamp + path + query + body
    return hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()


class DeltaClient:
    def __init__(self, base_url: str, api_key: str = "", api_secret: str = "", timeout: float = 15.0,
                 session: requests.Session | None = None):
        self.base = base_url.rstrip("/")
        self.key, self.secret = api_key, api_secret
        self.timeout = timeout
        self.http = session or requests.Session()
        self.time_offset = 0.0  # server time minus local time, seconds

    # ------------------------------------------------------------------ core
    def request(self, method: str, path: str, params: dict | None = None, body: dict | None = None,
                auth: bool = False, retries: int = 3):
        method = method.upper()
        query = "?" + urlencode(params, safe=",") if params else ""
        body_s = json.dumps(body, separators=(",", ":")) if body is not None else ""
        # never auto-retry order placement (could double the order); reads and cancels are safe to retry
        attempts = 1 if (method == "POST" and path.startswith("/v2/orders")) else retries
        last_exc = None
        for attempt in range(attempts):
            headers = {"User-Agent": "deltabot/0.1", "Accept": "application/json",
                       "Content-Type": "application/json"}
            if auth:
                if not (self.key and self.secret):
                    raise RuntimeError("API key/secret missing: put DELTA_API_KEY and DELTA_API_SECRET in .env")
                ts = str(int(time.time() + self.time_offset))
                headers.update({"api-key": self.key, "timestamp": ts,
                                "signature": sign(self.secret, method, ts, path, query, body_s)})
            try:
                r = self.http.request(method, self.base + path + query, data=body_s or None,
                                      headers=headers, timeout=self.timeout)
            except requests.RequestException as e:
                last_exc = e
                log.warning("network error on %s %s (attempt %d): %s", method, path, attempt + 1, e)
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code in (429, 500, 502, 503, 504) and attempt < attempts - 1:
                log.warning("HTTP %s on %s %s, retrying", r.status_code, method, path)
                time.sleep(2 * (attempt + 1))
                continue
            try:
                data = r.json()
            except ValueError:
                raise DeltaAPIError(r.status_code, {"error": r.text[:300]}, path)
            if r.status_code >= 400 or (isinstance(data, dict) and data.get("success") is False):
                raise DeltaAPIError(r.status_code, data, path)
            return data.get("result") if isinstance(data, dict) else data
        raise ConnectionError(f"{method} {path} failed after {attempts} attempts: {last_exc}")

    def sync_time(self) -> float:
        """Estimate server-clock offset from the HTTP Date header (signatures expire after a few seconds)."""
        t0 = time.time()
        r = self.http.get(self.base + "/v2/products/BTCUSD", headers={"User-Agent": "deltabot/0.1"},
                          timeout=self.timeout)
        t1 = time.time()
        if "Date" in r.headers:
            server = parsedate_to_datetime(r.headers["Date"]).timestamp()
            self.time_offset = server - (t0 + t1) / 2
            if abs(self.time_offset) > 2:
                log.warning("local clock is off by %.1fs vs Delta; compensating (sync your PC clock)",
                            self.time_offset)
            else:
                self.time_offset = 0.0
        return self.time_offset

    # ------------------------------------------------------------------ public
    def product(self, symbol: str) -> SymbolInfo:
        p = self.request("GET", f"/v2/products/{symbol}")
        return SymbolInfo(symbol=p["symbol"], product_id=int(p["id"]),
                          contract_value=float(p["contract_value"]), tick_size=float(p["tick_size"]))

    def ticker(self, symbol: str) -> dict:
        return self.request("GET", f"/v2/tickers/{symbol}")

    def candles(self, symbol: str, resolution: str, start: int, end: int) -> pd.DataFrame:
        """OHLCV candles between start and end (unix seconds), indexed by candle open time (UTC)."""
        step = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}[resolution]
        rows, chunk = [], 500 * step
        s = start
        while s < end:
            e = min(s + chunk, end)
            res = self.request("GET", "/v2/history/candles",
                               params={"resolution": resolution, "symbol": symbol, "start": s, "end": e}) or []
            rows += res
            s = e
        if not rows:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        df = pd.DataFrame(rows)
        df.index = pd.to_datetime(df["time"].astype(int), unit="s", utc=True)
        df = df[["open", "high", "low", "close", "volume"]].astype(float)
        return df[~df.index.duplicated(keep="last")].sort_index()

    # ------------------------------------------------------------------ private: account
    def balances(self) -> list[dict]:
        return self.request("GET", "/v2/wallet/balances", auth=True) or []

    def equity(self, asset: str = "USD") -> float:
        for b in self.balances():
            if str(b.get("asset_symbol", "")).upper() == asset.upper():
                return float(b.get("balance") or 0)
        raise RuntimeError(f"no {asset} wallet balance found; set account.margin_asset in settings.yaml")

    def position_size(self, product_id: int) -> int:
        """Signed contracts: + long, - short, 0 flat."""
        p = self.request("GET", "/v2/positions", params={"product_id": product_id}, auth=True) or {}
        return int(float(p.get("size") or 0))

    def set_leverage(self, product_id: int, leverage: int):
        return self.request("POST", f"/v2/products/{product_id}/orders/leverage",
                            body={"leverage": str(leverage)}, auth=True)

    # ------------------------------------------------------------------ private: orders
    def place_order(self, product_id: int, size: int, side: str, order_type: str = "market_order",
                    limit_price: float | None = None, reduce_only: bool = False, post_only: bool = False,
                    client_order_id: str | None = None, stop_price: float | None = None,
                    stop_trigger_method: str | None = None) -> dict:
        body = {"product_id": product_id, "size": int(size), "side": side, "order_type": order_type}
        if limit_price is not None:
            body["limit_price"] = str(limit_price)
        if reduce_only:
            body["reduce_only"] = True
        if post_only:
            body["post_only"] = True
        if client_order_id:
            body["client_order_id"] = client_order_id[:32]
        if stop_price is not None:
            body["stop_order_type"] = "stop_loss_order"
            body["stop_price"] = str(stop_price)
            if stop_trigger_method:
                body["stop_trigger_method"] = stop_trigger_method
        return self.request("POST", "/v2/orders", body=body, auth=True)

    def get_order(self, order_id: int) -> dict:
        return self.request("GET", f"/v2/orders/{order_id}", auth=True)

    def open_orders(self, product_id: int) -> list[dict]:
        return self.request("GET", "/v2/orders", params={"product_ids": product_id, "states": "open,pending"},
                            auth=True) or []

    def cancel_order(self, order_id: int, product_id: int) -> dict:
        return self.request("DELETE", "/v2/orders", body={"id": int(order_id), "product_id": product_id},
                            auth=True)

    def cancel_all(self, product_id: int):
        return self.request("DELETE", "/v2/orders/all",
                            body={"product_id": product_id, "cancel_limit_orders": True,
                                  "cancel_stop_orders": True}, auth=True)

    def fills(self, product_id: int, page_size: int = 20) -> list[dict]:
        return self.request("GET", "/v2/fills", params={"product_ids": product_id, "page_size": page_size},
                            auth=True) or []
