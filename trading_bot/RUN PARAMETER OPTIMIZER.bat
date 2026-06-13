@echo off
title Parameter Optimizer — Finding Best Thresholds
cd /d "%~dp0"

echo ============================================================
echo   PARAMETER OPTIMIZER
echo   Tests 72 threshold combinations on 60 watchlist stocks
echo   Runtime: ~5-20 minutes
echo ============================================================
echo.
echo Step 1: Install packages...
pip install openpyxl requests python-dotenv pytz -q
echo.
echo Step 2: Running grid search...
echo.
python parameter_optimizer.py
pause
