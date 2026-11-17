"""Alerts: always logged; also sent to Telegram when TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set."""
from __future__ import annotations

import logging

import requests

log = logging.getLogger("deltabot.notify")


class Notifier:
    def __init__(self, env: str, token: str = "", chat_id: str = ""):
        self.env, self.token, self.chat_id = env, token, chat_id

    def send(self, text: str, level: int = logging.INFO) -> None:
        log.log(level, text)
        if not (self.token and self.chat_id):
            return
        prefix = "🧪 TESTNET" if self.env == "testnet" else "💰 LIVE"
        try:
            requests.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                          json={"chat_id": self.chat_id, "text": f"{prefix}\n{text}"}, timeout=10)
        except requests.RequestException as e:
            log.warning("telegram send failed: %s", e)
