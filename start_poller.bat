@echo off
title Nuport Order Poller — Winterfell
echo ============================================================
echo  Winterfell: Nuport Order Poller
echo  Checks for new off-channel orders every 5 minutes
echo  Syncs status changes every 15 minutes
echo  Press Ctrl+C to stop
echo ============================================================
echo.
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Please install Python 3.10+ from python.org
    pause
    exit /b 1
)

pip install -r requirements.txt --quiet

echo Starting poller...
echo.
python order_poller.py

pause
