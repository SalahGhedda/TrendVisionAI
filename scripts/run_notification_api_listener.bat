@echo off
setlocal
cd /d "%~dp0\.."

if not exist .venv\Scripts\python.exe (
    echo ERROR: Setup has not been run yet.
    echo Double-click scripts\setup.bat first.
    pause
    exit /b 1
)

chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set PYTHONPATH=%CD%\src
.venv\Scripts\python.exe -m trendvision_ai.notification_api_listener --config config.json

if errorlevel 1 pause
