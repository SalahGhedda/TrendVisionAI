@echo off
setlocal
cd /d "%~dp0\.."

if not exist .venv\Scripts\python.exe (
    echo ERROR: Setup has not been run yet.
    echo Run scripts\setup.bat first.
    pause
    exit /b 1
)

.venv\Scripts\python.exe -c "import PySide6, openai, keyring, tzdata" >nul 2>&1
if errorlevel 1 (
    echo ERROR: Desktop UI dependencies are not installed yet.
    echo Run scripts\setup.bat after git pull.
    pause
    exit /b 1
)

set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set PYTHONPATH=%CD%\src
.venv\Scripts\python.exe -m trendvision_ai.desktop_ui_strategy_pipeline_v4
