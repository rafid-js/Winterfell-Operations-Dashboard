@echo off
:: setup_scheduler.bat
:: Registers both sync services as Windows Task Scheduler jobs.
:: Run this ONCE as Administrator (right-click → Run as administrator).
:: After setup: both services start automatically on login.

title Winterfell — Windows Task Scheduler Setup
echo ============================================================
echo  Winterfell Sync — Windows Task Scheduler Setup
echo  Run this as ADMINISTRATOR
echo ============================================================
echo.

:: Check admin rights
net session >nul 2>&1
if errorlevel 1 (
    echo ERROR: Please right-click this file and choose "Run as administrator"
    pause
    exit /b 1
)

cd /d "%~dp0"
set SCRIPT_DIR=%~dp0

:: Find Python executable
for /f "delims=" %%i in ('where python 2^>nul') do (
    set PYTHON_EXE=%%i
    goto :found_python
)
echo ERROR: Python not found in PATH.
echo Install Python from python.org and tick "Add to PATH" during install.
pause
exit /b 1

:found_python
echo Python found at: %PYTHON_EXE%
echo Script dir:      %SCRIPT_DIR%
echo.

:: Install dependencies
echo Installing Python dependencies...
"%PYTHON_EXE%" -m pip install -r "%SCRIPT_DIR%requirements.txt" --quiet
echo.

:: Remove old tasks if they exist
schtasks /delete /tn "WinterfellOrderPoller"     /f >nul 2>&1
schtasks /delete /tn "WinterfellWebhookListener" /f >nul 2>&1
schtasks /delete /tn "WinterfellStatusSync"      /f >nul 2>&1

:: Register order poller — starts on user login, runs continuously (self-schedules)
schtasks /create ^
    /tn "WinterfellOrderPoller" ^
    /tr "\"%PYTHON_EXE%\" \"%SCRIPT_DIR%order_poller.py\"" ^
    /sc ONLOGON ^
    /rl HIGHEST ^
    /f
if errorlevel 1 (
    echo FAILED to register WinterfellOrderPoller
) else (
    echo [OK] WinterfellOrderPoller — runs on login, polls every 5 min
)

echo.
echo ============================================================
echo  Setup complete!
echo  To start now (without rebooting):
echo    Double-click: start_listener.bat
echo    Double-click: start_status_sync.bat
echo.
echo  To manage tasks: open Task Scheduler, look for "Winterfell*"
echo ============================================================
pause
