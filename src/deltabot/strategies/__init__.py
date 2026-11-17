from deltabot.strategies.base import Strategy
from deltabot.strategies.trend_4h import Donchian4h, TrendEma4h
from deltabot.strategies.williams_intraday import WilliamsIntraday

REGISTRY: dict[str, type[Strategy]] = {c.name: c for c in (WilliamsIntraday, TrendEma4h, Donchian4h)}


def build(name: str, params: dict | None = None) -> Strategy:
    if name not in REGISTRY:
        raise ValueError(f"unknown strategy {name!r}; available: {sorted(REGISTRY)}")
    return REGISTRY[name](**(params or {}))


__all__ = ["REGISTRY", "Strategy", "build", "WilliamsIntraday", "TrendEma4h", "Donchian4h"]
