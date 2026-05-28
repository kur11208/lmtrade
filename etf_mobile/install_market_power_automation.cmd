@echo off
setlocal

set "ROOT=%~dp0"
set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"

net session >nul 2>&1
if not "%errorlevel%"=="0" (
  echo Requesting administrator permission...
  "%PS%" -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

echo Enabling Wake-on-LAN for Killer E2400 Gigabit Ethernet Controller...
powercfg /deviceenablewake "Killer E2400 Gigabit Ethernet Controller"

echo Registering LMTrade scheduled tasks...
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%ROOT%register_market_power_tasks.ps1"

echo.
echo Current wake-enabled devices:
powercfg /devicequery wake_armed

echo.
echo Done. Configure Windows auto logon separately, then test Wake-on-LAN from phone.
pause
