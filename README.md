# deltabot

Trading bot for **Delta Exchange India** BTCUSD and ETHUSD perpetual futures.
It runs **paper-first on the Delta India demo (testnet) account**.

## Strategies in use (BTC and ETH)

- **BTCUSD:** only **`ema_pullback`** (4h, `src/deltabot/strategies/pullback.py`).
- **ETHUSD:** only **`trend_rider`** (4h, `src/deltabot/strategies/trend_rider.py`, boost 1.5): the same trades as
  `ema_pullback`, sized by setup quality (see below). `ema_pullback` is turned off for ETH.

Live-settings backtest, Jun 2024-Nov 2026 from $10k (`live_sim`, 2% risk, one shared balance, hourly mark-to-market
drawdown, Williams off): BTC ema_pullback + ETH trend_rider $82.6k (worst drop -28.6%); both ema_pullback $64.4k
(-29.8%); both trend_rider $74.2k (-31.0%); BTC trend_rider + ETH ema_pullback $52.0k (-28.4%). This split was picked
after seeing these results, so expect less going forward.

**`williams_intraday`** (1h, k=0.7) is still listed in the symbol configs but turned off (`enabled: false`, since
2026-11-22). When on, it trades only while `ema_pullback` is flat and is always flat by 00:00 UTC (05:30 IST); BTC
uses `preempt: true` (an `ema_pullback` signal closes a running Williams trade), ETH `preempt: false`. It added only
a little in backtests (see the table below).

`ema_pullback` rules:

- **Trend:** EMA 50 above EMA 100 = up-trend, below = down-trend. ADX(14) must be above 20.
- **Entry 1, the cross:** go long/short on the 4h close where EMA 50 crosses EMA 100.
- **Entry 2, the pullback:** inside the trend, wait for a candle to touch EMA 50 (close still on the trend side of
  EMA 100); within the next 6 candles, enter on a reversal candle that closes back past EMA 50, past the previous
  candle's high (low for shorts) and in the trend's colour.
- **Stop-loss** (placed on Delta, never moved): beyond the last 5 candles' swing low/high plus 0.25 ATR, kept 1-3 ATR away.
- **Exit:** the opposite EMA 50/100 cross on a closed 4h candle, or the stop. No take-profit, no trailing stop.

Backtest at 1% risk, $10,000 start, Delta fees, slippage and funding included:

| Symbol | Setup | Profit | CAGR | Max DD | Trades | Win % |
|---|---|---|---|---|---|---|
| BTC 2020-26 | **ema_pullback alone (now)** | $63,889 | 33.8% | -8.9% | 130 | 27% |
| BTC 2020-26 | supertrend_4h > tep_4h > williams_intraday (before) | $18,934 | 16.7% | -18.1% | 572 | 37% |
| ETH Apr 2024-Nov 2026 | **ema_pullback alone (now)** | $10,413 | 29.2% | -12.4% | 51 | 22% |
| ETH Apr 2024-Nov 2026 | supertrend_4h > tep_4h > williams_intraday, preempt (before) | $3,559 | 11.6% | -11.9% | 207 | 40% |
| BTC 2020-26 | ema_pullback > williams k=0.7, preempt on (until 2026-11-22) | $69,804 | 35.3% | -10.3% | 214 | 35% |
| ETH Apr 2024-Nov 2026 | ema_pullback > williams k=0.7, preempt off (until 2026-11-22) | $12,740 | 34.4% | -13.1% | 90 | 36% |

The rows marked "now" are the bot as configured. Williams added little: about +$4,100 on BTC and +$950 on ETH, with a
slightly deeper drawdown. In the intraday sweep (`scripts/intraday_research.py`, 484 setups: EMA/VWAP pullbacks,
opening-range breakouts, Williams) 95% lost money on BTC 2024-26 after fees; Williams k=0.7 was the best of them.

Settings were chosen on BTC 2020-23 only. Every 4h EMA-50 variant tested (50/100, 50/120, any pullback depth,
with or without ADX) was profitable on unseen BTC 2024-26, and ETH is entirely unseen data. Faster pairs did worse
(9/21 lost money on ETH). Expect long losing streaks (up to 8 losses in a row): most profit comes from a few
trend trades lasting weeks to months. Adding Supertrend or TEP behind it lowered the BTC result because they block
its trend trades (Delta keeps one net position per contract).

The older strategies (`supertrend_4h`, `tep_4h`, `trend_ema_4h`, `donchian_4h`) are no
longer in the symbol configs, but their code is kept; add an entry back to `config/symbols/<SYMBOL>.yaml` to use one
(listed in priority order, see `preempt`). **TEP** is this bot's own indicator (`indicators.trend_efficiency_pressure`).

**`trend_rider`** (`src/deltabot/strategies/trend_rider.py`) is **on for ETHUSD** and listed but off
(`enabled: false`) for BTCUSD. It takes the same trades as `ema_pullback` and sizes each one by setup quality: x0.5 risk on
EMA-cross entries, x0.25 when ADX > 35 or volatility is in its top third, all scaled by `boost: 1.5`. Other rule
changes (entry filters, regime filter, breakouts, tighter exits) failed on unseen data; only the sizing held up.
With the live settings (`scripts/live_backtest.py`: 2% risk, shared balance, hourly mark-to-market drawdown), BTC+ETH
Jun 2024-Nov 2026 from $10k ended at $74.2k (both coins on trend_rider) vs $65.9k for ema_pullback > williams, both with a -31% worst drop. On BTC
2020-26 the two tie (the 2000-contract cap binds). To switch a symbol, set `trend_rider` to `enabled: true` and
`ema_pullback` to `enabled: false` in its symbol config, then restart the bot; never enable both.
Note: the drawdowns in the table above are closed-trade only; measured hourly on open positions they are about 3x larger.

Research scripts: `scripts/rider_research.py` and `scripts/rider_compare.py` (trend_rider sizing),
`scripts/exit_research.py` (exit variants), `scripts/combo_research.py` (indicator combinations, settings, timeframes) and
`scripts/pullback_research.py` (ema_pullback sweep). Results are written to `data/research/`.

Run `python -m deltabot backtest` (BTC) or `python -m deltabot backtest --symbol ETHUSD --start 2024-01-01` to
reproduce these numbers. The backtest uses the same strategy classes as the live bot.

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

## Running in the background (server / instance)

`scripts/run_bot.bat` runs the bot in a loop: it restarts the bot 30 s after a crash and exits for good when
`deltabot stop` writes `data\KILL`. `deltabot run` deletes that file when it starts, so the next start works normally.

**Run only one copy at a time.** Two copies would trade on the same account. The instance's public IP must also be
whitelisted on the Delta API key.

### Option A: quick background run (stops when you log off or reboot)

```powershell
# start (no window)
Start-Process -FilePath "C:\Personal project\delta-bot\scripts\run_bot.bat" -WindowStyle Hidden

# stop: exits within ~30 s, exchange stop-losses stay in place
cd "C:\Personal project\delta-bot"; .venv\Scripts\python.exe -m deltabot stop

# emergency stop: also cancels all orders and closes all positions
cd "C:\Personal project\delta-bot"; .venv\Scripts\python.exe -m deltabot stop --flatten
```

### Option B: scheduled task (recommended; survives logoff and starts at boot)

Register it once from PowerShell **run as Administrator**. `ExecutionTimeLimit` must be zero, otherwise Windows kills
the task after 72 hours:

```powershell
$a = New-ScheduledTaskAction -Execute "C:\Personal project\delta-bot\scripts\run_bot.bat"
$t = New-ScheduledTaskTrigger -AtStartup
$s = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "DeltaBot" -Action $a -Trigger $t -Settings $s -User "SYSTEM" -RunLevel Highest
```

| Action | Command |
|---|---|
| Start | `Start-ScheduledTask -TaskName DeltaBot` |
| Stop (clean) | `.venv\Scripts\python.exe -m deltabot stop` (from the project folder) |
| Force kill (last resort) | `Stop-ScheduledTask -TaskName DeltaBot` |
| Is it running? | `Get-ScheduledTask -TaskName DeltaBot` (State = Running) |
| Don't start at boot | `Disable-ScheduledTask -TaskName DeltaBot` |
| Remove the task | `Unregister-ScheduledTask -TaskName DeltaBot -Confirm:$false` |

After a clean `stop`, the task still starts the bot on the next reboot unless you disable it. Prefer `stop` over a
force kill. A force kill is still safe because stop-losses live on Delta and state is saved in
`data\state_testnet.json`.

### Checking on it

```powershell
Get-Content "C:\Personal project\delta-bot\logs\bot.log" -Tail 30 -Wait   # live log
.venv\Scripts\python.exe -m deltabot status                              # equity, positions, orders
```

`logs\service.log` records each crash restart and the final KILL stop.

### Linux instance

```bash
cd ~/delta-bot && nohup .venv/bin/python -m deltabot run >> logs/console.log 2>&1 &   # start
cd ~/delta-bot && .venv/bin/python -m deltabot stop                                   # stop
```

For auto-restart and start at boot on Linux, use a systemd service instead of `nohup`.

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
