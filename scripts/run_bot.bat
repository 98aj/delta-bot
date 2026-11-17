@echo off
rem Keeps the bot running on a Windows server. Restarts it 30s after a crash.
rem Exits for good when "deltabot stop" has written data\KILL.
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
if not exist logs mkdir logs

:loop
".venv\Scripts\python.exe" -m deltabot run
if exist "data\KILL" goto end
echo %date% %time% bot exited, restarting in 30s >> logs\service.log
ping -n 31 127.0.0.1 >nul
goto loop

:end
echo %date% %time% stopped by KILL file >> logs\service.log
