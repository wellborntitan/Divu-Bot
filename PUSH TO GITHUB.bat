@echo off
title Push to GitHub (FULL RESET)
cd /d "C:\Users\harit\OneDrive\Desktop\Trading Strategy\StocksBreakout"

echo.
echo  =====================================================
echo    Pushing Albert Ray Bot to GitHub  (FULL RESET)
echo    For normal day-to-day pushes use PUSH UPDATE.bat
echo  =====================================================
echo.

:: Syntax-check Python before touching git
python -m py_compile trading_bot\main.py trading_bot\run_job.py trading_bot\position_monitor.py trading_bot\data_fetcher.py trading_bot\trade_executor.py trading_bot\pattern_detector.py trading_bot\discord_notifier.py trading_bot\indicators.py trading_bot\risk_manager.py trading_bot\config.py
if errorlevel 1 (
    echo.
    echo  PYTHON SYNTAX ERROR — push cancelled. See error above.
    pause
    goto END
)

:: Remove any broken .git folder from previous attempts
if exist ".git" (
    echo  Cleaning up old git folder...
    rd /s /q ".git"
)

:: Fresh init
echo  [1/6] Initialising git repo...
git init -b main
if errorlevel 1 goto ERROR

:: Set identity (required for commit)
git config user.email "haritk2103@gmail.com"
git config user.name "Harit"

:: Set remote
echo  [2/6] Setting remote origin...
git remote add origin https://github.com/wellborntitan/Divu-Bot.git
if errorlevel 1 goto ERROR

:: Preserve the bot's latest positions file from GitHub (the bot
:: commits positions.json from Actions, so the remote copy is fresher)
echo  [3/6] Grabbing bot's latest positions.json from GitHub...
git fetch origin main
if not errorlevel 1 (
    git checkout origin/main -- trading_bot/positions.json 2>nul
)

:: Stage everything
echo  [4/6] Staging all files...
git add .
if errorlevel 1 goto ERROR

:: Commit
echo  [5/6] Committing...
git commit -m "bot update (full reset)"
if errorlevel 1 goto ERROR

:: Force push -- local is the source of truth after a reset
echo  [6/6] Pushing to GitHub (force)...
echo  (A browser window may open to log in to GitHub)
echo.
git push --force -u origin main

if errorlevel 1 goto ERROR

echo.
echo  =====================================================
echo    SUCCESS! Code is live at:
echo    https://github.com/wellborntitan/Divu-Bot
echo  =====================================================
echo.
pause
goto END

:ERROR
echo.
echo  Something went wrong. See error above.
pause

:END
