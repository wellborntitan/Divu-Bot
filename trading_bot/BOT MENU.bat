@echo off
title Albert Ray Trading Bot
cd /d "%~dp0"

:MENU
cls
echo.
echo  =====================================================
echo    ALBERT RAY TRADING BOT
echo  =====================================================
echo.
echo    1.  Start Live Bot         (runs every day, paper trades)
echo.
echo    2.  Run Backtest           (3 years history, ~30-90 min)
echo.
echo    3.  Optimize Bot           (reads backtest, tunes settings)
echo.
echo    4.  Full Tune Cycle        (Backtest + Optimize in one go)
echo.
echo    5.  Test Discord           (send a test alert)
echo.
echo  =====================================================
echo.
set /p CHOICE=  Enter number (1-5):

if "%CHOICE%"=="1" goto START_BOT
if "%CHOICE%"=="2" goto BACKTEST
if "%CHOICE%"=="3" goto OPTIMIZE
if "%CHOICE%"=="4" goto FULL_TUNE
if "%CHOICE%"=="5" goto DISCORD
goto MENU

:START_BOT
cls
echo  Starting live bot...
echo  (Close this window to stop the bot)
echo.
pip install -r requirements.txt -q
python main.py
goto END

:BACKTEST
cls
echo  Running 3-year backtest...
echo  Results saved to: Backtest_Results.xlsx
echo.
pip install openpyxl -q
python backtest.py
echo.
echo  Done! Open Backtest_Results.xlsx to see results.
echo  Run option 3 next to apply the findings to the bot.
pause
goto MENU

:OPTIMIZE
cls
echo  Optimizing bot from backtest results...
echo.
pip install openpyxl -q
python optimize_bot.py
echo.
echo  Done! Restart the bot (option 1) to use new settings.
pause
goto MENU

:FULL_TUNE
cls
echo  Step 1/2: Running backtest...
echo.
pip install openpyxl -q
python backtest.py
echo.
echo  Step 2/2: Optimizing bot...
echo.
python optimize_bot.py
echo.
echo  All done! Start the bot with option 1.
pause
goto MENU

:DISCORD
cls
echo  Sending test Discord alert...
echo.
python test_discord.py
pause
goto MENU

:END
pause
