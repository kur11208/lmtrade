@echo off
cd /d C:\work\lmtrade\phone_deploy
echo Serving ETF mobile update files on http://0.0.0.0:8765/
echo Keep this window open while updating the phone.
python -m http.server 8765 --bind 0.0.0.0
