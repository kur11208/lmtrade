@echo off
setlocal

if not "%~1"=="" set "ETF_PHONE_HOST=%~1"
if "%ETF_PHONE_HOST%"=="" set "ETF_PHONE_HOST=192.168.0.96"
if "%ETF_PHONE_USER%"=="" set "ETF_PHONE_USER=u0_a31"
if "%ETF_PHONE_KEY%"=="" set "ETF_PHONE_KEY=%USERPROFILE%\.ssh\etf_mobile_phone_ed25519"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy_to_phone.ps1" -Restart

