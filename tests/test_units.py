import hashlib
import hmac

import pandas as pd

from deltabot.data.candles import complete_bars
from deltabot.exchange.rest import SymbolInfo, sign
from deltabot.portfolio.journal import trade_pnl
from deltabot.risk.sizing import contracts_for_risk


def test_signature_matches_delta_spec():
    secret, ts = "s3cret", "1700000000"
    body = '{"product_id":27,"size":1}'
    expected = hmac.new(secret.encode(), ("POST" + ts + "/v2/orders" + "" + body).encode(),
                        hashlib.sha256).hexdigest()
    assert sign(secret, "POST", ts, "/v2/orders", "", body) == expected
    q = "?product_id=27"
    assert sign(secret, "GET", ts, "/v2/positions", q, "") == hmac.new(
        secret.encode(), ("GET" + ts + "/v2/positions" + q).encode(), hashlib.sha256).hexdigest()


def test_sizing_by_risk_and_caps():
    # $10k equity, 1% risk = $100; $1,000 stop distance x 0.001 BTC = $1/contract -> 100 contracts
    assert contracts_for_risk(10_000, 92_000, 91_000, 0.001, 0.01, 3.0, 10_000) == 100
    # very tight stop: leverage cap 3x -> 30,000 / 92 = 326 contracts
    assert contracts_for_risk(10_000, 92_000, 91_990, 0.001, 0.01, 3.0, 10_000) == 326
    assert contracts_for_risk(10_000, 92_000, 91_990, 0.001, 0.01, 3.0, 50) == 50
    assert contracts_for_risk(10_000, 92_000, 92_000, 0.001, 0.01, 3.0, 50) == 0


def test_round_price_direction():
    s = SymbolInfo("BTCUSD", 1, 0.001, 0.5)
    assert s.round_price(100.3, "down") == 100.0
    assert s.round_price(100.3, "up") == 100.5


def test_trade_pnl_includes_fees():
    pnl, fees = trade_pnl(1, 100, 0.001, 90_000, 91_000)
    assert round(fees, 4) == round(0.1 * (90_000 + 91_000) * 0.0005 * 1.18, 4)
    assert round(pnl, 4) == round(100 - fees, 4)
    pnl_s, _ = trade_pnl(-1, 100, 0.001, 90_000, 91_000)
    assert pnl_s < 0


def test_complete_4h_bars_drop_partial():
    idx = pd.date_range("2026-01-01", periods=7, freq="1h", tz="UTC")
    h1 = pd.DataFrame({"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 1.0}, index=idx)
    b4 = complete_bars(h1, 4)
    assert list(b4.index) == [pd.Timestamp("2026-01-01 00:00", tz="UTC")]
