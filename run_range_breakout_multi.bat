@echo off
echo ========================================
echo Multi-Symbol Backtest: range_breakout_v1
echo Symbols: XAUUSD, XAGUSD, BTCUSD, EURUSD
echo Period: 2020-01-01 to 2024-12-31
echo ========================================
echo.

cd /d "D:\Works\ASTRA ANALYZER CHART"

python -u scripts/run_multi_symbol_backtest.py --strategy range_breakout_v1 > range_breakout_multi_log.txt 2>&1

echo.
echo ========================================
echo Backtest complete! Check range_breakout_multi_log.txt
echo ========================================
pause
