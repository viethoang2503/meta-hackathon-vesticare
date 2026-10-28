@echo off
REM Vesti-Care — Run Simulator (Windows)
REM Usage:
REM   start_simulator.bat                  -> one_fall scenario (default)
REM   start_simulator.bat normal
REM   start_simulator.bat one_fall
REM   start_simulator.bat multi_fall
REM   start_simulator.bat bad_payload
REM   start_simulator.bat all

cd /d "%~dp0"

if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
) else (
    echo [ERROR] Virtual environment not found. Run setup.bat first.
    pause
    exit /b 1
)

set SCENARIO=%1
if "%SCENARIO%"=="" set SCENARIO=one_fall

if "%SCENARIO%"=="all" (
    echo Running all scenarios...
    python backend\simulator\run_scenario.py --all
) else (
    echo Running scenario: %SCENARIO%
    python backend\simulator\run_scenario.py --scenario %SCENARIO%
)
