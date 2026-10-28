@echo off
REM Vesti-Care — Start Everything

cd /d "%~dp0"
set ROOT=%~dp0

echo ============================================
echo   VERTI-GO ALERT - Khoi dong he thong
echo ============================================
echo.

REM --- Kiem tra venv ---
if not exist ".venv\Scripts\activate.bat" (
    echo [ERROR] Chua co virtual environment!
    echo Chay: setup.bat truoc.
    pause
    exit /b 1
)

REM --- Kiem tra .env ---
if not exist "backend\.env" (
    echo [WARNING] backend\.env chua co, dang copy tu template...
    copy "backend\.env.example" "backend\.env" >nul
    echo [OK] Da tao backend\.env
    echo.
)

REM --- Chon scenario simulator ---
echo Chon scenario de chay simulator:
echo   1. normal       - Hoat dong binh thuong
echo   2. one_fall     - 1 lan nga (mac dinh)
echo   3. multi_fall   - Nhieu lan nga
echo   4. all          - Chay tat ca scenario
echo   5. Bo qua (chi chay backend va dashboard)
echo.
set /p CHOICE="Nhap so lua chon (1-5, mac dinh=2): "

if "%CHOICE%"=="1" set SCENARIO=normal
if "%CHOICE%"=="2" set SCENARIO=one_fall
if "%CHOICE%"=="3" set SCENARIO=multi_fall
if "%CHOICE%"=="4" set SCENARIO=all
if "%CHOICE%"=="5" set SCENARIO=skip
if "%CHOICE%"==""  set SCENARIO=one_fall

echo.

REM --- Khoi dong Backend trong terminal rieng ---
echo [1/3] Dang khoi dong Backend tai http://localhost:8000 ...
start "Vesti-Care Backend" cmd /k "cd /d %ROOT% && call .venv\Scripts\activate.bat && cd backend && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"

REM --- Kiem tra backend co san sang khong (toi da 30 giay) ---
echo     Dang kiem tra backend (toi da 30 giay)...
set /a WAITED=0
:wait_loop
powershell -Command "try { (Invoke-WebRequest -Uri 'http://localhost:8000/api/alerts/stats' -UseBasicParsing -TimeoutSec 2).StatusCode } catch { 0 }" 2>nul | findstr "200" >nul
if %errorlevel%==0 goto backend_ready
set /a WAITED+=1
if %WAITED% geq 30 goto backend_timeout
timeout /t 1 /nobreak >nul
goto wait_loop
:backend_ready
echo     [OK] Backend san sang sau %WAITED% giay.
goto backend_done
:backend_timeout
echo     [WARNING] Backend chua phan hoi sau 30 giay, tiep tuc...
:backend_done

REM --- Mo Dashboard trong trinh duyet ---
echo [2/3] Dang mo Dashboard trong trinh duyet...
start "" "%ROOT%dashboard\index.html"

REM --- Chay Simulator ---
if "%SCENARIO%"=="skip" (
    echo [3/3] Bo qua simulator.
) else if "%SCENARIO%"=="all" (
    echo [3/3] Dang chay tat ca scenarios...
    start "Vesti-Care Simulator" cmd /k "cd /d %ROOT% && call .venv\Scripts\activate.bat && python backend\simulator\run_scenario.py --all && pause"
) else (
    echo [3/3] Dang chay simulator: %SCENARIO%
    start "Vesti-Care Simulator" cmd /k "cd /d %ROOT% && call .venv\Scripts\activate.bat && python backend\simulator\run_scenario.py --scenario %SCENARIO% && pause"
)

echo.
echo ============================================
echo   He thong da khoi dong!
echo   Backend  : http://localhost:8000
echo   API Docs : http://localhost:8000/docs
echo   Health   : http://localhost:8000/api/sensor/health
echo   Dashboard: da mo trong trinh duyet
echo ============================================
echo.
echo Lenh reset demo : python demo_reset.py
echo                   demo_reset.bat
echo.
echo De dung he thong: dong cac cua so terminal.
pause
