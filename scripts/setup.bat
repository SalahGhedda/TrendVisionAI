@echo off
setlocal
cd /d "%~dp0\.."

echo [1/4] Checking Python...
py -3 --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python 3 was not found.
    echo Install Python 3 from https://www.python.org/downloads/windows/
    pause
    exit /b 1
)

echo [2/4] Creating virtual environment...
if not exist .venv (
    py -3 -m venv .venv
)

echo [3/4] Installing dependencies...
call .venv\Scripts\python.exe -m pip install --upgrade pip
call .venv\Scripts\python.exe -m pip install -r requirements.txt

if not exist config.json copy /Y config.example.json config.json >nul

echo [4/4] Done.
echo.
echo Next: double-click scripts\run_listener.bat
pause
