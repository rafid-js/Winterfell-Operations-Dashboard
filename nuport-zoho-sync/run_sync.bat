@echo off
title Winterfell - Nuport to Zoho Sync
echo ============================================================
echo   Winterfell Operations - Nuport to Zoho Books Sync
echo ============================================================
echo.
echo Starting sync... this usually takes 30-60 seconds.
echo.

cd /d "%~dp0"

python sync_nuport_zoho.py

if %errorlevel% neq 0 (
    echo.
    echo ============================================================
    echo   ERROR: Sync failed. Check the logs\ folder for details.
    echo ============================================================
) else (
    echo.
    echo ============================================================
    echo   Done! Check the logs\ folder for the full report.
    echo ============================================================
)

echo.
pause
