"""Confirm best M15 config still works, get year-by-year breakdown."""
import sys
sys.path.insert(0, 'D:/OneDrive/Рабочий стол/work/AstraAnalyzerPro')
from astra_v2 import config
from astra_v2.data.dukascopy import load_timeframe
from astra_v2.backtest.engine import run_backtest
from astra_v2.data.external import fetch_yfinance_bulk, fetch_cot_gold
from astra_v2.data.fred_client import fetch_all as fetch_fred_bulk

h4_full = load_timeframe('H4', start='2020-01-01', end='2024-12-31')
bars_full = load_timeframe('M15', start='2020-01-01', end='2024-12-31')
fred = fetch_fred_bulk('2020-01-01', '2024-12-31')
yf = fetch_yfinance_bulk('2020-01-01', '2024-12-31', cache_only=True)
cot = fetch_cot_gold(cache_only=True)

# Best found config: fvg_min=3.0, TRENDING+ACCUM, touch_boundary, wf=3+1
config.FVG_MIN_SIZE_ATR = 3.0
config.SMC_FVG_V1_ENTRY_MODE = 'touch_boundary'
config.SMC_FVG_V1_FVG_TIMEFRAME = 'M15'
config.SMC_FVG_V1_TP_RR = 2.0
config.SMC_FVG_V1_PARTIAL_CLOSE_RR = 1.0
config.SMC_FVG_V1_STOP_BUFFER_ATR = 1.5
config.SMC_FVG_V1_ENTRY_DEPTH_ATR = 1.0
config.SMC_FVG_V1_ALLOWED_REGIMES = ('TRENDING', 'ACCUMULATION')

result = run_backtest(
    bars=bars_full, fred_df=fred, yfinance_df=yf, cot_df=cot,
    m1_bars=None, h4_bars=h4_full,
    mode='proxy', strategy_id='smc_fvg_v1',
    start_balance=10000.0, wf_train_months=3, wf_test_months=1
)
s = result.summary()
print("=== BEST CONFIG VERIFICATION ===")
print("FVG_MIN=3.0, touch_boundary, TRENDING+ACCUM, wf=3+1")
print("n={} WR={:.1%} PF={:.3f} DD={:.2f}% final_bal={:.0f}".format(
    s['total_trades'], s['win_rate'], s['profit_factor'],
    s['max_drawdown_pct'], s['final_balance']))

closed = [t for t in result.trades if t.status != 'open']
wins = [t for t in closed if t.dollar_pnl > 0]
losses = [t for t in closed if t.dollar_pnl < 0]
tp_hits = [t for t in closed if t.status == 'tp']
sl_hits = [t for t in closed if t.status in ('sl', 'be_sl')]
fc = [t for t in closed if t.status == 'forced']
print("tp={} sl={} fc={} be_sl={}".format(
    len(tp_hits), len([t for t in closed if t.status == 'sl']),
    len(fc), len([t for t in closed if t.status == 'be_sl'])))
aw = sum(t.dollar_pnl for t in wins) / len(wins) if wins else 0
al = sum(t.dollar_pnl for t in losses) / len(losses) if losses else 0
print("avg_win={:.0f} avg_loss={:.0f}".format(aw, al))

print()
print("Year-by-year breakdown:")
for year in [2020, 2021, 2022, 2023, 2024]:
    yr_trades = [t for t in closed if t.opened_at and t.opened_at.year == year]
    if not yr_trades:
        continue
    wr = sum(1 for t in yr_trades if t.dollar_pnl > 0) / len(yr_trades)
    pnl = sum(t.dollar_pnl for t in yr_trades)
    tp_yr = sum(1 for t in yr_trades if t.status == 'tp')
    sl_yr = sum(1 for t in yr_trades if t.status in ('sl', 'be_sl'))
    print("  {}: n={} WR={:.1%} pnl={:.0f} tp={} sl={}".format(
        year, len(yr_trades), wr, pnl, tp_yr, sl_yr))

print()
print("Direction breakdown:")
for direction in ['BULLISH', 'BEARISH']:
    dt = [t for t in closed if t.direction == direction]
    if not dt:
        continue
    wr = sum(1 for t in dt if t.dollar_pnl > 0) / len(dt)
    pnl = sum(t.dollar_pnl for t in dt)
    print("  {}: n={} WR={:.1%} pnl={:.0f}".format(direction, len(dt), wr, pnl))
