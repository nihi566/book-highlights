@echo off
cd /d %~dp0
echo Starting Kindle Pulse server...
python src/server.py
pause
