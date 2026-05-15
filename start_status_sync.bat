@echo off
title Nuport Status Sync — Winterfell
echo ============================================================
echo  Winterfell: Nuport Status Sync
echo  Runs every 15 minutes (configurable in config.json)
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

echo Starting status sync...
echo.
python status_sync.py

pause
