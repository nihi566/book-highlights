@echo off
cd /d %~dp0
echo Building frontend...
cd frontend
call npm run build
cd ..
echo Starting Kindle Pulse server...
python src/server.py
pause
