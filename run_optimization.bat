@echo off
cd /d "D:\Works\ASTRA ANALYZER CHART"
echo Killing old python processes...
taskkill /F /IM python.exe 2>nul
timeout /t 2 /nobreak >nul
echo Starting optimization...
python -u scripts/optimize_breakout_retest.py > optimization_log2.txt 2>&1
pause
