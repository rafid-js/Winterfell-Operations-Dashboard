@echo off
title Nuport Webhook Listener — Winterfell
echo ============================================================
echo  Winterfell: Nuport to WooCommerce Webhook Listener
echo  Port: 5000
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

echo Starting listener...
echo.
python webhook_listener.py

pause
