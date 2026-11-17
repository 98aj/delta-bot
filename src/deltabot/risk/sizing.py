"""Position sizing.

Two modes:
  risk mode  (fixed_contracts = 0): risk a fixed % of equity between entry and stop.
  fixed mode (fixed_contracts > 0): always trade that many contracts.
Both are capped by the leverage limit, max_contracts, and max_trade_risk_pct (the most any single
trade may lose at its stop, as % of equity).
"""
from __future__ import annotations

import math


def contracts_for_risk(equity: float, entry: float, stop: float, contract_value: float, risk_pct: float,
                       max_leverage: float, max_contracts: int, min_contracts: int = 0,
                       max_trade_risk_pct: float = 0.05, fixed_contracts: int = 0) -> int:
    """Example (risk mode): equity $10,000, risk 1% = $100; entry 92,000, stop 91,000 -> $1 per contract
    (0.001 BTC) -> 100 contracts = 0.1 BTC = $9,200 notional (0.92x leverage, under the 3x cap).

    Small accounts: if risk mode rounds below min_contracts, trade min_contracts anyway, as long as the
    real risk stays at or under max_trade_risk_pct and within the leverage cap.
    """
    dist = abs(entry - stop)
    if equity <= 0 or dist <= 0 or entry <= 0:
        return 0
    per_contract_risk = dist * contract_value
    by_leverage = math.floor(equity * max_leverage / (entry * contract_value))
    by_trade_cap = math.floor(equity * max_trade_risk_pct / per_contract_risk)
    hard_cap = max(0, min(by_leverage, by_trade_cap, max_contracts))
    if fixed_contracts > 0:
        return min(fixed_contracts, hard_cap)
    n = max(0, math.floor(min(equity * risk_pct / per_contract_risk, by_leverage, max_contracts)))
    if n < min_contracts and min_contracts <= hard_cap:
        return min_contracts
    return n
