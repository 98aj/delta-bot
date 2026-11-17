"""Settings, environment (.env) and Delta India endpoints."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(os.environ.get("DELTABOT_HOME", Path(__file__).resolve().parents[2]))

ENDPOINTS = {
    "testnet": {"rest": "https://cdn-ind.testnet.deltaex.org", "ws": "wss://socket-ind.testnet.deltaex.org"},
    "live": {"rest": "https://api.india.delta.exchange", "ws": "wss://socket.india.delta.exchange"},
}

# Delta India futures fees (taker 0.05% + 18% GST), used for P&L estimates and the backtest
TAKER_FEE = 0.0005 * 1.18
MAKER_FEE = 0.0002 * 1.18


def load_dotenv(path: Path) -> None:
    """Minimal .env reader: KEY=VALUE lines; existing environment variables win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


@dataclass
class StrategyConf:
    name: str
    params: dict = field(default_factory=dict)
    enabled: bool = True


@dataclass
class SymbolConf:
    symbol: str
    strategies: list[StrategyConf]
    enabled: bool = True
    max_contracts: int = 1000
    fixed_contracts: int = 0      # 0 = size by risk_pct; >0 = always trade this many contracts (still capped)


@dataclass
class Settings:
    env: str = "testnet"
    api_key: str = ""
    api_secret: str = ""
    risk_pct: float = 0.01
    max_leverage: float = 3.0
    exchange_leverage: int = 5
    daily_loss_limit_pct: float = 0.03
    min_contracts: int = 1
    max_trade_risk_pct: float = 0.05
    margin_asset: str = "USD"
    stop_trigger: str = "last_traded_price"
    market_data: str = "live"      # where candles for signals come from: live (public) or testnet
    poll_seconds: int = 30
    bar_settle_seconds: int = 15
    history_days: int = 120
    max_signal_age_minutes: int = 10
    allow_live: bool = False
    symbols: list[SymbolConf] = field(default_factory=list)
    data_dir: Path = ROOT / "data"
    log_dir: Path = ROOT / "logs"
    telegram_token: str = ""
    telegram_chat_id: str = ""

    @property
    def rest_url(self) -> str:
        return ENDPOINTS[self.env]["rest"]

    @property
    def ws_url(self) -> str:
        return ENDPOINTS[self.env]["ws"]

    @property
    def has_keys(self) -> bool:
        return bool(self.api_key and self.api_secret)


def load_symbol(config_dir: Path, symbol: str) -> SymbolConf:
    raw = yaml.safe_load((config_dir / "symbols" / f"{symbol}.yaml").read_text(encoding="utf-8"))
    strats = [StrategyConf(name=s["name"], params=s.get("params") or {}, enabled=s.get("enabled", True))
              for s in raw.get("strategies", [])]
    return SymbolConf(symbol=raw.get("symbol", symbol), strategies=strats,
                      enabled=raw.get("enabled", True), max_contracts=int(raw.get("max_contracts", 1000)),
                      fixed_contracts=int(raw.get("fixed_contracts", 0) or 0))


def load_settings(root: Path | None = None) -> Settings:
    root = Path(root or ROOT)
    load_dotenv(root / ".env")
    raw = yaml.safe_load((root / "config" / "settings.yaml").read_text(encoding="utf-8")) or {}
    risk, acct, exe = raw.get("risk", {}), raw.get("account", {}), raw.get("execution", {})
    loop, safety = raw.get("loop", {}), raw.get("safety", {})
    env = os.environ.get("DELTA_ENV", "testnet").strip().lower()
    if env not in ENDPOINTS:
        raise ValueError(f"DELTA_ENV must be 'testnet' or 'live', got {env!r}")
    s = Settings(
        env=env,
        api_key=os.environ.get("DELTA_API_KEY", "").strip(),
        api_secret=os.environ.get("DELTA_API_SECRET", "").strip(),
        risk_pct=float(risk.get("risk_pct", 0.01)),
        max_leverage=float(risk.get("max_leverage", 3.0)),
        exchange_leverage=int(risk.get("exchange_leverage", 5)),
        daily_loss_limit_pct=float(risk.get("daily_loss_limit_pct", 0.03)),
        min_contracts=int(risk.get("min_contracts", 1)),
        max_trade_risk_pct=float(risk.get("max_trade_risk_pct", risk.get("min_lot_max_risk_pct", 0.05))),
        margin_asset=str(acct.get("margin_asset", "USD")),
        stop_trigger=str(exe.get(f"stop_trigger_{env}", exe.get("stop_trigger", "last_traded_price"))),
        market_data=str(raw.get("market_data", {}).get("source", "live")),
        poll_seconds=int(loop.get("poll_seconds", 30)),
        bar_settle_seconds=int(loop.get("bar_settle_seconds", 15)),
        history_days=int(loop.get("history_days", 120)),
        max_signal_age_minutes=int(loop.get("max_signal_age_minutes", 10)),
        allow_live=bool(safety.get("allow_live", False)),
        symbols=[load_symbol(root / "config", sym) for sym in raw.get("symbols", [])],
        data_dir=root / "data",
        log_dir=root / "logs",
        telegram_token=os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(),
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", "").strip(),
    )
    if s.market_data not in ENDPOINTS:
        raise ValueError("market_data.source must be 'live' or 'testnet'")
    if not 0 < s.max_trade_risk_pct <= 0.10:
        raise ValueError("max_trade_risk_pct must be between 0 and 10%")
    for sc in s.symbols:
        if sc.fixed_contracts < 0:
            raise ValueError(f"{sc.symbol}: fixed_contracts must be 0 or more")
    if not 0 < s.risk_pct <= 0.05:
        raise ValueError("risk_pct must be between 0 and 5%")
    if not 0 < s.max_leverage <= 10:
        raise ValueError("max_leverage must be between 0 and 10")
    return s
