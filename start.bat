@echo off
echo ============================================
echo   Permafrost (PF) - AI Brain Framework
echo ============================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python 3.10+ first.
    echo Download: https://python.org/downloads
    pause
    exit /b 1
)

:: Install dependencies
echo [1/3] Installing dependencies...
pip install -r "%~dp0requirements.txt" -q >nul 2>&1

:: Create data directory
if not exist "%USERPROFILE%\.permafrost" mkdir "%USERPROFILE%\.permafrost"

:: Start console + brain
echo [2/3] Starting web console + brain...
start "" "http://localhost:8503"
echo [3/3] Opening browser...
cd /d "%~dp0"

:: Start brain in background if config exists
if exist "%USERPROFILE%\.permafrost\config.json" (
    start /B python launcher.py
)

:: Start console (foreground)
cd /d "%~dp0console"
streamlit run app.py --server.port 8503 --server.headless true
