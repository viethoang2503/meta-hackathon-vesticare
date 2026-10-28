@echo off
REM Vesti-Care — First-time Setup (Windows)
REM Run once after cloning the repo: setup.bat

cd /d "%~dp0"

echo ============================================
echo   Vesti-Care — Environment Setup
echo ============================================
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.12 from https://python.org
    pause
    exit /b 1
)

for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo [OK] Python %PYVER% found.

REM Create virtual environment
if not exist ".venv" (
    echo Creating virtual environment...
    python -m venv .venv
    echo [OK] Virtual environment created at .venv\
) else (
    echo [OK] Virtual environment already exists.
)

REM Activate
call .venv\Scripts\activate.bat

REM Install dependencies
echo Installing dependencies...
pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo [ERROR] pip install failed. Check requirements.txt and your internet connection.
    pause
    exit /b 1
)
echo [OK] Dependencies installed.

REM Set up .env
if not exist "backend\.env" (
    copy "backend\.env.example" "backend\.env" >nul
    echo [OK] Created backend\.env from template.
    echo      Edit backend\.env to add your Telegram bot token and chat ID.
) else (
    echo [OK] backend\.env already exists.
)

echo ============================================
echo   Setup complete.
echo.
echo   Next steps:
echo     1. Edit backend\.env  (add Telegram credentials if needed)
echo     2. Run start_backend.bat  to start the server
echo     3. Open dashboard\index.html in your browser
echo     4. Run start_simulator.bat to stream test data
echo ============================================
pause
