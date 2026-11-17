"""Logging to console and logs/bot.log, timestamps in IST."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))


class ISTFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        return datetime.fromtimestamp(record.created, IST).strftime("%Y-%m-%d %H:%M:%S IST")


def setup_logging(log_dir: Path, env: str, level: int = logging.INFO) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = ISTFormatter(f"%(asctime)s  [{env}] %(levelname)-7s %(message)s")
    root = logging.getLogger("deltabot")
    root.setLevel(level)
    root.handlers.clear()
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    fileh = RotatingFileHandler(log_dir / "bot.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    fileh.setFormatter(fmt)
    root.addHandler(console)
    root.addHandler(fileh)
    return root
