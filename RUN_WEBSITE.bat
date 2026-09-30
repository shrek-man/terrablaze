@echo off
setlocal
cd /d "%~dp0"

echo ================================================
echo   THERMALTRACE - INDIA FIRE MONITORING
echo ================================================
echo.

where py >nul 2>nul
if errorlevel 1 (
  echo Python launcher ^(py^) was not found.
  echo Install Python 3.11 or newer from python.org and try again.
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
.venv\Scripts\python.exe -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
  echo.
  echo Package installation failed. Check your internet connection.
  pause
  exit /b 1
)

echo.
echo The NASA FIRMS key can be entered in the website. It is not needed for demo mode.
echo Starting the website at http://127.0.0.1:8000 ...
start "" powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "%~dp0open_browser.ps1"
.venv\Scripts\python.exe -m uvicorn backend:app --host 127.0.0.1 --port 8000

if errorlevel 1 (
  echo.
  echo The website stopped with an error. Read the message above for details.
  pause
)
