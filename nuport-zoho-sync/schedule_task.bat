@echo off
title Winterfell - Setup Scheduled Task
echo ============================================================
echo   Winterfell - Setup Auto-Sync (runs every 6 hours)
echo ============================================================
echo.
echo This will schedule the sync to run automatically every 6 hours.
echo You only need to run this ONCE.
echo.
echo NOTE: If this fails, right-click this file and choose
echo       "Run as Administrator" then try again.
echo.

set SCRIPT_DIR=%~dp0
set PYTHON_SCRIPT=%SCRIPT_DIR%sync_nuport_zoho.py
set TASK_NAME=Winterfell Nuport-Zoho Sync

schtasks /create ^
  /tn "%TASK_NAME%" ^
  /tr "python \"%PYTHON_SCRIPT%\"" ^
  /sc hourly ^
  /mo 6 ^
  /st 06:00 ^
  /ru "%USERNAME%" ^
  /f

if %errorlevel% equ 0 (
    echo.
    echo ============================================================
    echo   SUCCESS!
    echo   Sync is now scheduled to run every 6 hours from 6:00 AM.
    echo   Task name: %TASK_NAME%
    echo.
    echo   To check it: Press Win+R, type taskschd.msc, press Enter
    echo   To remove it: Run the command below in Command Prompt:
    echo     schtasks /delete /tn "%TASK_NAME%" /f
    echo ============================================================
) else (
    echo.
    echo ============================================================
    echo   FAILED to create scheduled task.
    echo   Please right-click this file and "Run as Administrator".
    echo ============================================================
)

echo.
pause
