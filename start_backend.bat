
@echo off
REM Vesti-Care — Start Backend (Windows)
REM Run this from the project root: start_backend.bat

cd /d "%~dp0"

REM Activate virtual environment
if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
) else (
    echo [ERROR] Virtual environment not found at .venv\
    echo Run setup.bat first to set up the environment.
    pause
    exit /b 1
)

REM Check .env file
if not exist "backend\.env" (
    echo [WARNING] backend\.env not found.
    echo Copying from backend\.env.example — edit it to add Telegram credentials.
    copy "backend\.env.example" "backend\.env" >nul
)

REM Start backend
cd backend
echo Starting Vesti-Care backend on http://localhost:8000 ...
echo API docs: http://localhost:8000/docs
echo Press Ctrl+C to stop.
echo.
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

