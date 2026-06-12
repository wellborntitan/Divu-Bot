@echo off
title Backtester - Albert Ray Strategies
cd /d "C:\Users\harit\OneDrive\Desktop\Trading Strategy\StocksBreakout\trading_bot"

echo ============================================================
echo   WALK-FORWARD BACKTESTER  ^|  3 Years  ^|  Full Market
echo ============================================================
echo.
echo Installing required packages...
pip install openpyxl requests python-dotenv pytz apscheduler -q
echo.
echo ============================================================
echo   STARTING BACKTEST — this will take 30-90 minutes
echo   Results saved to: StocksBreakout\Backtest_Results.xlsx
echo   Progress prints below. A cache file saves your progress
echo   so you can safely restart if anything interrupts.
echo ============================================================
echo.
python backtest.py
pause
