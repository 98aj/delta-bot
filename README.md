# deltabot

Trading bot for **Delta Exchange India** BTCUSD perpetual futures. ETH is ready to enable later.
It runs **paper-first on the Delta India demo (testnet) account**.

## Strategies (backtested 2020-2026, Delta fees, slippage and funding included)

| Strategy | Timeframe | Trades | PF | Total | Max DD | Notes |
|---|---|---|---|---|---|---|
| `trend_ema_4h` | 4h | ~14/yr | 2.14 | +72% | -6.7% | EMA 20/50 + ADX + EMA200, 3 ATR trailing stop |
| `supertrend_4h` | 4h | ~22/yr | 1.98 | +145% | -14.5% | Supertrend 20,4 flip; all 30 settings profitable on BTC and ETH |
| `williams_intraday` | 1h | ~2/week | 1.10 | +44% | -20.9% | flat by 05:30 IST; weak in 2025 (-14%) |
| `donchian_4h` (off) | 4h | ~1/week | 1.38 | +194% | -18.4% | alternative trend system |

Delta keeps **one net position per contract**, so only one strategy holds BTCUSD at a time. Priority follows the order in `config/symbols/BTCUSD.yaml`.
Strategies can block each other, so the **combined** result is what counts (`deltabot backtest` prints it):

| Enabled, priority order | CAGR | Max DD | PF 2025-26 |
|---|---|---|---|
| trend_ema_4h > supertrend_4h > williams_intraday | 15.8% | -17.8% | 1.22 |
| **supertrend_4h > williams_intraday (default)** | 19.6% | -16.9% | 1.31 |
| supertrend_4h only | 13.9% | -14.5% | 2.28 |
| trend_ema_4h > williams_intraday (old) | 5.5% | -21.2% | 0.96 |

Run `python -m deltabot backtest` to reproduce these numbers. The backtest uses the same strategy classes as the live bot.

## Setup (Windows, PowerShell)

```powershell
cd "C:\Personal project\delta-bot"
.venv\Scripts\activate          # already created; otherwise: python -m venv .venv; pip install -e .[dev]
copy .env.example .env          # then open .env in an editor and paste your DEMO key + secret
```

Create the API key inside the **demo** account: Account → API Keys, **trading permission only, no withdrawals**, and whitelist your IP.

## Commands

```powershell
python -m deltabot check        # connection, product, balance, positions
python -m deltabot test-order   # testnet: places a tiny far-away limit order and cancels it
python -m deltabot run          # start the bot (Ctrl+C to stop; exchange stops stay in place)
python -m deltabot status       # equity, positions, open orders
python -m deltabot report --days 30
python -m deltabot stop --flatten   # kill switch: stop bot, cancel orders, close positions
python -m deltabot resume BTCUSD    # after fixing a position mismatch
python -m pytest -q                 # tests
```

## How it trades
- It checks Delta every 30 seconds. When a 1h candle closes, it runs the strategies on that closed bar only.
- Entry is a market order, followed by a **reduce-only stop-loss order on Delta**. If the stop can't be placed, the bot closes the position.
- Size: `risk_pct` of equity at risk between entry and stop (currently 2%), capped at `max_leverage` (5x)
  and at `max_contracts`. Exchange leverage is set to 10x at startup (margin only).
  To change it, raise `risk_pct` in `config/settings.yaml` (0.02 = double), or set a fixed lot with
  `fixed_contracts: N` in `config/symbols/BTCUSD.yaml`. Every mode keeps a minimum of 1 contract and is capped
  so no single trade can lose more than `max_trade_risk_pct` (5%) of equity.
- After a daily loss of 3%, there are no new entries until 00:00 UTC.
- Each poll it compares its records with Delta's real position:
  - a stop-loss fill is recorded automatically;
  - any unexplained position (for example a manual trade) halts that symbol until you run `resume`.
- It never acts on a candle that closed more than 10 minutes ago, so a restart can't take a late entry.
- State is saved in `data/state_testnet.json`, so a restart picks up open positions.
- Trades are stored in `data/trades_testnet.db` and logs in `logs/bot.log`.
- Optional Telegram alerts: set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`.

## Paper → live checklist
1. Paper trade for 6–8 weeks and at least 50 trades. Compare `report` with the backtest.
2. Get the tax treatment confirmed by a CA.
3. Create a separate **live** API key (trading only, no withdrawals, IP whitelist). Set `DELTA_ENV=live` and set `safety.allow_live: true`.
4. Start small: lower `max_contracts` and `risk_pct`.
5. Run `python -m deltabot run --live` and type `LIVE` to confirm.

Not financial advice. Trading leveraged derivatives can lose more than expected.
