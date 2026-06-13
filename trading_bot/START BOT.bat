@echo off
title Trading Bot
cd /d "%~dp0"
echo ============================================
echo   Trading Bot - Starting...
echo ============================================
echo.

:: Kill any existing bot instance to prevent double-scheduling
echo Checking for existing bot process...
taskkill /F /FI "WINDOWTITLE eq Trading Bot" /FI "IMAGENAME eq python.exe" >nul 2>&1
timeout /t 2 /nobreak >nul

echo Installing/updating packages...
pip install -r requirements.txt -q
echo.
echo Launching bot...
echo.
python main.py
pause
