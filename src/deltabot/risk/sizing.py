"""Position sizing: risk a fixed % of equity between entry and stop, capped by leverage and a contract limit."""
from __future__ import annotations

import math


def contracts_for_risk(equity: float, entry: float, stop: float, contract_value: float, risk_pct: float,
                       max_leverage: float, max_contracts: int) -> int:
    """Whole contracts so that hitting the stop loses about risk_pct of equity.

    Example: equity $10,000, risk 1% = $100; entry 92,000, stop 91,000 -> $1 per contract (0.001 BTC)
    -> 100 contracts = 0.1 BTC = $9,200 notional (0.92x leverage, under the 3x cap).
    """
    dist = abs(entry - stop)
    if equity <= 0 or dist <= 0 or entry <= 0:
        return 0
    by_risk = equity * risk_pct / (dist * contract_value)
    by_leverage = equity * max_leverage / (entry * contract_value)
    return max(0, int(math.floor(min(by_risk, by_leverage, max_contracts))))
