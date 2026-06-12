@echo off
title Push Update to GitHub
cd /d "C:\Users\harit\OneDrive\Desktop\Trading Strategy\StocksBreakout"

echo.
echo  =====================================================
echo    Pushing update to GitHub
echo  =====================================================
echo.

if not exist ".git" (
    echo  No git repo found. Run "PUSH TO GITHUB.bat" first.
    pause
    goto END
)

echo  [0/4] Syntax-checking Python files...
python -m py_compile trading_bot\main.py trading_bot\run_job.py trading_bot\position_monitor.py trading_bot\data_fetcher.py trading_bot\trade_executor.py trading_bot\pattern_detector.py trading_bot\discord_notifier.py trading_bot\indicators.py trading_bot\risk_manager.py trading_bot\config.py
if errorlevel 1 (
    echo.
    echo  PYTHON SYNTAX ERROR — push cancelled. See error above.
    pause
    goto END
)

echo  [1/4] Staging changes...
git add -A

echo  [2/4] Committing...
git commit -m "update: local changes"
if errorlevel 1 echo  (Nothing new to commit -- continuing)

echo  [3/4] Syncing with GitHub (bot's positions.json wins on conflict)...
git pull --rebase -X ours origin main
if errorlevel 1 goto ERROR

echo  [4/4] Pushing...
git push origin main
if errorlevel 1 goto ERROR

echo.
echo  =====================================================
echo    Done! Update is live.
echo  =====================================================
echo.
pause
goto END

:ERROR
echo.
echo  Something went wrong. See error above.
echo  If a rebase is stuck, run:  git rebase --abort
pause

:END
