"""Test H4 FVG mode."""
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

tests = [
    ('touch_boundary', 'H4', 2.0, 0.5, 1.0, ('TRENDING', 'ACCUMULATION')),
    ('touch_boundary', 'H4', 2.0, 1.5, 1.0, ('TRENDING', 'ACCUMULATION')),
    ('touch_boundary', 'H4', 1.5, 0.5, 1.0, ('TRENDING', 'ACCUMULATION')),
    ('wick_rejection', 'H4', 2.0, 0.5, 1.0, ('TRENDING', 'ACCUMULATION')),
    ('wick_rejection', 'H4', 2.0, 1.5, 1.0, ('TRENDING', 'ACCUMULATION')),
    ('touch_boundary', 'H4', 2.0, 0.5, 2.0, ('TRENDING', 'ACCUMULATION')),
]

print('H4 FVG mode tests (wf=3+1, 2020-2024):')
for entry_mode, fvg_tf, tp_rr, stop_buf, depth, regimes in tests:
    config.SMC_FVG_V1_ENTRY_MODE = entry_mode
    config.SMC_FVG_V1_FVG_TIMEFRAME = fvg_tf
    config.FVG_MIN_SIZE_ATR = 1.5
    config.SMC_FVG_V1_TP_RR = tp_rr
    config.SMC_FVG_V1_PARTIAL_CLOSE_RR = tp_rr * 0.5
    config.SMC_FVG_V1_STOP_BUFFER_ATR = stop_buf
    config.SMC_FVG_V1_ENTRY_DEPTH_ATR = depth
    config.SMC_FVG_V1_ALLOWED_REGIMES = regimes
    result = run_backtest(
        bars=bars_full, fred_df=fred, yfinance_df=yf, cot_df=cot,
        m1_bars=None, h4_bars=h4_full,
        mode='proxy', strategy_id='smc_fvg_v1',
        start_balance=10000.0, wf_train_months=3, wf_test_months=1
    )
    s = result.summary()
    closed = [t for t in result.trades if t.status != 'open']
    tp_hits = sum(1 for t in closed if t.status == 'tp')
    sl = sum(1 for t in closed if t.status in ('sl', 'be_sl'))
    print('  {} {} tp={} sb={} d={} r={}: n={} WR={:.1%} PF={:.3f} DD={:.2f}% tp={} sl={}'.format(
        entry_mode[:5], fvg_tf, tp_rr, stop_buf, depth, regimes[0][:5],
        s['total_trades'], s['win_rate'], s['profit_factor'], s['max_drawdown_pct'], tp_hits, sl))
