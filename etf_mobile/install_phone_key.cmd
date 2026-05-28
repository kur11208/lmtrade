@echo off
setlocal

if not "%~1"=="" set "ETF_PHONE_HOST=%~1"
if "%ETF_PHONE_HOST%"=="" set "ETF_PHONE_HOST=192.168.0.96"
if "%ETF_PHONE_USER%"=="" set "ETF_PHONE_USER=u0_a31"
if "%ETF_PHONE_KEY%"=="" set "ETF_PHONE_KEY=%USERPROFILE%\.ssh\etf_mobile_phone_ed25519"

if not exist "%ETF_PHONE_KEY%.pub" (
  echo public key not found: %ETF_PHONE_KEY%.pub
  exit /b 1
)

type "%ETF_PHONE_KEY%.pub" | ssh -p 8022 -oPubkeyAuthentication=no -oPreferredAuthentications=password,keyboard-interactive %ETF_PHONE_USER%@%ETF_PHONE_HOST% "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat > ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && echo key_installed"

