@echo off
REM demo_reset.bat — Xoa data demo, giu lai data nghien cuu
cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
    echo [ERROR] Chua co virtual environment. Chay setup.bat truoc.
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat
python demo_reset.py %*
pause
