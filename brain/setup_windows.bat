@echo off
echo === Winterfell Brain — Windows Setup ===
echo.

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python 3 not found.
    echo         Download and install from: https://python.org/downloads
    echo         Make sure to check "Add Python to PATH" during install.
    pause
    exit /b 1
)

echo [OK] Python found:
python --version
echo.

echo Installing required packages...
echo.
pip install psycopg2-binary sqlalchemy pgvector python-dotenv requests

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Package installation failed.
    echo         Try running this window as Administrator, or run:
    echo         pip install --user psycopg2-binary sqlalchemy pgvector python-dotenv
    pause
    exit /b 1
)

echo.
echo [OK] All packages installed successfully
echo [OK] Setup complete — ready to connect to Railway
echo.
echo Next steps:
echo   1. Make sure brain\.env has your DATABASE_URL filled in
echo   2. Run:  python brain\test_connection.py
echo   3. Run:  python brain\create_tables.py
echo   4. Run:  python brain\health_check.py
echo.
pause
