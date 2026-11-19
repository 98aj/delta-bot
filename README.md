# deltabot

Trading bot for **Delta Exchange India** BTCUSD and ETHUSD perpetual futures.
It runs **paper-first on the Delta India demo (testnet) account**.

## Strategies (backtested 2020-2026, Delta fees, slippage and funding included)

| Strategy | Timeframe | Trades | PF | Total | Max DD | Notes |
|---|---|---|---|---|---|---|
| `trend_ema_4h` | 4h | ~14/yr | 2.14 | +72% | -6.7% | EMA 20/50 + ADX + EMA200, 3 ATR trailing stop |
| `supertrend_4h` | 4h | ~22/yr | 1.98 | +145% | -14.5% | Supertrend 20,4 flip; all 30 settings profitable on BTC and ETH |
| `williams_intraday` | 1h | ~2/week | 1.10 | +44% | -20.9% | flat by 05:30 IST; weak in 2025 (-14%) |
| `donchian_4h` (off) | 4h | ~1/week | 1.38 | +194% | -18.4% | alternative trend system |
| `tep_4h` | 4h | ~1/week | 1.43 | +154% | -16.5% | own indicator TEP (below); settings chosen on 2020-23 only, PF 1.42 on unseen BTC 2024-26 |

**TEP (Trend Efficiency Pressure)** is this bot's own indicator (`indicators.trend_efficiency_pressure`):
net price progress over the last 20 bars divided by the total distance travelled, each bar weighted by its
relative volume, EMA-smoothed. +1 = clean up-trend carried by heavy-volume bars, 0 = chop, -1 = clean down-trend.
`tep_4h` goes long when TEP crosses above +0.4, short below -0.4, exits when TEP crosses 0 (2 ATR stop, 3 ATR trail).
Its trades barely overlap with Supertrend's (daily P&L correlation about 0.05).

Delta keeps **one net position per contract**, so only one strategy holds a symbol at a time. Priority follows the
order in `config/symbols/<SYMBOL>.yaml`: **the 4h strategies (Supertrend, then TEP) come first, and Williams only
trades while no 4h strategy holds the position.**

`preempt` (per symbol) decides what happens when a 4h signal arrives while Williams is already in a trade:
`false` = the 4h signal is skipped until Williams is flat; `true` = the bot closes the Williams trade and the 4h
strategy takes over. A lower-priority strategy never replaces a higher one.

Strategies can block each other, so the **combined** result is what counts (`deltabot backtest` prints it).
BTC 2020-26 at 1% risk:

| Enabled, priority order | CAGR | Max DD | PF 2025-26 |
|---|---|---|---|
| **supertrend_4h > tep_4h > williams_intraday, preempt off (current)** | 17.4% | -18.1% | 1.37 |
| same, preempt on | 15.7% | -21.3% | 1.15 |
| supertrend_4h > tep_4h (no Williams) | 17.2% | -13.1% | 1.96 |
| supertrend_4h > williams_intraday (previous default) | 19.6% | -16.9% | 1.31 |
| trend_ema_4h > supertrend_4h > williams_intraday | 15.8% | -17.8% | 1.22 |
| supertrend_4h only | 13.9% | -14.5% | 2.28 |

ETH (Delta data Feb 2024 - Nov 2026, 1% risk), supertrend_4h > tep_4h > williams_intraday with **preempt on
(current)**: 11.1%/yr, max DD -11.9%, PF 1.28. At 2% risk preempt on/off: ~21%/yr DD -22.6% vs ~8%/yr DD -28.6%.

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
