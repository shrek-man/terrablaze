@echo off
setlocal
cd /d "%~dp0"

echo ================================================
echo   THERMAL INTELLIGENCE - SIH 2026
ECHO ================================================
echo.

where py >nul 2>nul
if errorlevel 1 (
  echo Python launcher ^(py^) was not found.
  echo Install Python 3.11+ from python.org and try again.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating local Python environment...
  py -3 -m venv .venv
  if errorlevel 1 (
    echo Failed to create the virtual environment.
    pause
    exit /b 1
  )
)

echo Installing required packages...
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo Package installation failed. Check your internet connection.
  pause
  exit /b 1
)

echo.
echo Starting the website...
start "" cmd /c "timeout /t 3 /nobreak >nul && start http://127.0.0.1:8000"
.venv\Scripts\python.exe -m uvicorn backend:app --host 127.0.0.1 --port 8000

pause
