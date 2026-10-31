@echo off
setlocal

if not "%~1"=="" set "ETF_PHONE_HOST=%~1"
if "%ETF_PHONE_HOST%"=="" set "ETF_PHONE_HOST=192.168.0.xxx"
if "%ETF_PHONE_USER%"=="" set "ETF_PHONE_USER=termux_user"
if "%ETF_PHONE_KEY%"=="" set "ETF_PHONE_KEY=%USERPROFILE%\.ssh\example_phone_ed25519"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy_to_phone.ps1" -Restart

