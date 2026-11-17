"""Trade journal in SQLite (data/trades_<env>.db) and performance summaries."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from deltabot.config import TAKER_FEE

COLUMNS = ["symbol", "strategy", "side", "size", "entry_time", "entry_price", "exit_time", "exit_price",
           "initial_stop", "risk_usd", "fees_usd", "pnl_usd", "r_multiple", "reason", "env"]


def trade_pnl(side: int, size: int, contract_value: float, entry: float, exit_: float) -> tuple[float, float]:
    """(net P&L in USD after estimated taker fees on both legs, fees). Funding is not included."""
    qty = size * contract_value
    gross = side * qty * (exit_ - entry)
    fees = qty * (entry + exit_) * TAKER_FEE
    return gross - fees, fees


class Journal:
    def __init__(self, path: Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.execute(f"""CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT, {', '.join(c + ' TEXT' if c in
            ('symbol', 'strategy', 'entry_time', 'exit_time', 'reason', 'env') else c + ' REAL' for c in COLUMNS)})""")
        self.db.commit()

    def add(self, **row) -> None:
        self.db.execute(f"INSERT INTO trades ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})",
                        [row.get(c) for c in COLUMNS])
        self.db.commit()

    def trades(self, since: pd.Timestamp | None = None) -> pd.DataFrame:
        df = pd.read_sql_query("SELECT * FROM trades ORDER BY id", self.db)
        if df.empty:
            return df
        df["exit_time"] = pd.to_datetime(df.exit_time, utc=True)
        df["entry_time"] = pd.to_datetime(df.entry_time, utc=True)
        if since is not None:
            df = df[df.exit_time >= since]
        return df

    def realized_since(self, since: pd.Timestamp) -> float:
        t = self.trades(since)
        return float(t.pnl_usd.sum()) if not t.empty else 0.0


def summarize(t: pd.DataFrame) -> dict:
    if t.empty:
        return {"trades": 0}
    wins, losses = t[t.pnl_usd > 0], t[t.pnl_usd <= 0]
    gl = -losses.pnl_usd.sum()
    return {
        "trades": len(t),
        "win_rate_pct": round(100 * len(wins) / len(t), 1),
        "profit_factor": round(wins.pnl_usd.sum() / gl, 2) if gl > 0 else float("inf"),
        "total_R": round(t.r_multiple.sum(), 2),
        "avg_R": round(t.r_multiple.mean(), 3),
        "pnl_usd": round(t.pnl_usd.sum(), 2),
        "fees_usd": round(t.fees_usd.sum(), 2),
    }
