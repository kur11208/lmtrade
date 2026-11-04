@echo off
setlocal
set "PUBLISH_DIR=%~dp0..\phone_deploy"
if not exist "%PUBLISH_DIR%\etf_mobile_pull.sha256.hmac" (
  echo [ERROR] No signed manifest. Run phone_deploy\publish_phone_update.ps1 with the HMAC key configured.
  exit /b 1
)
if "%LMTRADE_UPDATE_BIND_HOST%"=="" (
  echo [ERROR] Set LMTRADE_UPDATE_BIND_HOST to the PC's trusted LAN IP, not 0.0.0.0.
  exit /b 1
)
if "%LMTRADE_UPDATE_BIND_HOST%"=="0.0.0.0" (
  echo [ERROR] Refusing to serve on every interface.
  exit /b 1
)
cd /d "%PUBLISH_DIR%"
echo Serving Git-tracked, signed update artifacts on %LMTRADE_UPDATE_BIND_HOST%:8765
python -m http.server 8765 --bind "%LMTRADE_UPDATE_BIND_HOST%"
