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


def test_min_lot_for_small_account():
    # $196, stop $2,851 away: 1% risk = $1.96 < $2.85 per contract -> normally 0
    assert contracts_for_risk(196, 65_000, 62_149, 0.001, 0.01, 3.0, 2000) == 0
    # with min 1 contract: risk 1.45% <= 5% cap -> 1 contract
    assert contracts_for_risk(196, 65_000, 62_149, 0.001, 0.01, 3.0, 2000, 1, 0.05) == 1
    # stop so wide that 1 contract would risk > 5% ($10 of $196) -> still skipped
    assert contracts_for_risk(196, 65_000, 55_000, 0.001, 0.01, 3.0, 2000, 1, 0.05) == 0
    # big accounts unaffected
    assert contracts_for_risk(10_000, 92_000, 91_000, 0.001, 0.01, 3.0, 10_000, 1, 0.05) == 100


def test_fixed_lot_mode_and_caps():
    # fixed 5 contracts, $10k, stop $1,000 away -> $5 risk (0.05%) -> 5 contracts
    assert contracts_for_risk(10_000, 65_000, 64_000, 0.001, 0.01, 3.0, 2000, 1, 0.05, fixed_contracts=5) == 5
    # $196 account: leverage cap 3x = $588 -> 9 contracts max, so fixed 20 becomes 9
    #   (stop $500 away: 9 x $0.5 = $4.5 = 2.3% risk, under the 5% cap)
    assert contracts_for_risk(196, 65_000, 64_500, 0.001, 0.01, 3.0, 2000, 1, 0.05, fixed_contracts=20) == 9
    # risk cap: stop $3,000 away = $3/contract; 5% of $196 = $9.8 -> at most 3 contracts
    assert contracts_for_risk(196, 65_000, 62_000, 0.001, 0.01, 3.0, 2000, 1, 0.05, fixed_contracts=20) == 3
    # max_contracts still applies
    assert contracts_for_risk(10_000, 65_000, 64_000, 0.001, 0.01, 3.0, 4, 1, 0.05, fixed_contracts=5) == 4


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
