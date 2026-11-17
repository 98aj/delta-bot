"""Bot state saved to disk after every change, so a restart picks up exactly where it left off."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Position:
    symbol: str
    strategy: str
    side: int                 # +1 long, -1 short
    size: int                 # contracts
    entry_price: float
    entry_time: str           # ISO UTC
    stop: float
    initial_stop: float
    risk_usd: float
    stop_order_id: int | None = None


@dataclass
class State:
    positions: dict[str, Position] = field(default_factory=dict)   # symbol -> position (one per symbol)
    halted: dict[str, str] = field(default_factory=dict)            # symbol -> reason
    last_bar: dict[str, str] = field(default_factory=dict)          # symbol -> last processed 1h bar (ISO)
    last_report_day: str = ""


class StateStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.state = self._load()

    def _load(self) -> State:
        if not self.path.exists():
            return State()
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        return State(positions={k: Position(**v) for k, v in raw.get("positions", {}).items()},
                     halted=raw.get("halted", {}), last_bar=raw.get("last_bar", {}),
                     last_report_day=raw.get("last_report_day", ""))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = asdict(self.state)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)   # atomic: never leaves a half-written state file

    # convenience
    @property
    def positions(self) -> dict[str, Position]:
        return self.state.positions

    def set_position(self, pos: Position) -> None:
        self.state.positions[pos.symbol] = pos
        self.save()

    def clear_position(self, symbol: str) -> None:
        self.state.positions.pop(symbol, None)
        self.save()

    def halt(self, symbol: str, reason: str) -> None:
        self.state.halted[symbol] = reason
        self.save()

    def resume(self, symbol: str) -> None:
        self.state.halted.pop(symbol, None)
        self.save()
