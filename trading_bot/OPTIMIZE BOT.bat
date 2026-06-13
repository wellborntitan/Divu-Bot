@echo off
title Bot Optimizer — Albert Ray Strategies
cd /d "%~dp0"

echo ============================================================
echo   BOT OPTIMIZER
echo   Reads Backtest_Results.xlsx and tunes the bot
echo ============================================================
echo.
echo NOTE: Run this AFTER the backtest finishes.
echo Then restart START BOT.bat to use the improved settings.
echo.
python optimize_bot.py
pause
