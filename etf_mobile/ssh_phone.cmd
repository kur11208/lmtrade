@echo off
ssh -p 8022 -i "%USERPROFILE%\.ssh\etf_mobile_phone_ed25519" -oIdentitiesOnly=yes u0_a31@192.168.0.96 "echo ok"
