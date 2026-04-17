"""FVG grid search v2 — focused on fvg_min=3.0 + TRENDING+ACCUM baseline."""
import sys
sys.path.insert(0, 'D:/OneDrive/Рабочий стол/work/AstraAnalyzerPro')

from astra_v2 import config
from astra_v2.data.dukascopy import load_timeframe
from astra_v2.backtest.engine import run_backtest
from astra_v2.data.external import fetch_yfinance_bulk, fetch_cot_gold
from astra_v2.data.fred_client import fetch_all as fetch_fred_bulk

print("Loading data...")
h4_full = load_timeframe('H4', start='2020-01-01', end='2024-12-31')
bars_full = load_timeframe('M15', start='2020-01-01', end='2024-12-31')
fred = fetch_fred_bulk('2020-01-01', '2024-12-31')
yf = fetch_yfinance_bulk('2020-01-01', '2024-12-31', cache_only=True)
cot = fetch_cot_gold(cache_only=True)
print("Data loaded.")

# Baseline: fvg_min=3.0, TRENDING+ACCUM, wf=3+1
# WR=50.4%, PF=0.953, DD=6.41%
# Goal: push PF > 1.4, DD < 5%

results = []

for tp_rr in [1.5, 2.0]:
    for partial_rr_mode in ['no_partial', 'half_tp']:
        for stop_buf in [0.5, 1.0, 1.5]:
            for depth in [0.5, 1.0, 1.5]:
                config.FVG_MIN_SIZE_ATR = 3.0
                config.SMC_FVG_V1_ENTRY_DEPTH_ATR = depth
                config.SMC_FVG_V1_STOP_BUFFER_ATR = stop_buf
                config.SMC_FVG_V1_TP_RR = tp_rr
                config.SMC_FVG_V1_ALLOWED_REGIMES = ('TRENDING', 'ACCUMULATION')
                if partial_rr_mode == 'no_partial':
                    config.SMC_FVG_V1_PARTIAL_CLOSE_RR = tp_rr
                else:
                    config.SMC_FVG_V1_PARTIAL_CLOSE_RR = tp_rr * 0.5

                result = run_backtest(
                    bars=bars_full, fred_df=fred, yfinance_df=yf, cot_df=cot,
                    m1_bars=None, h4_bars=h4_full,
                    mode='proxy', strategy_id='smc_fvg_v1',
                    start_balance=10000.0, wf_train_months=3, wf_test_months=1
                )
                s = result.summary()
                closed = [t for t in result.trades if t.status != 'open']
                tp_hits = sum(1 for t in closed if t.status == 'tp')
                sl_hits = sum(1 for t in closed if t.status in ('sl', 'be_sl'))
                fc = sum(1 for t in closed if t.status == 'forced')
                results.append((s['profit_factor'], s, tp_rr, partial_rr_mode, stop_buf, depth, tp_hits, sl_hits, fc))
                print('tp={} p={} sb={} d={}: n={:3d} WR={:.1%} PF={:.3f} DD={:.2f}% tp_h={} sl={} fc={}'.format(
                    tp_rr, partial_rr_mode[:4], stop_buf, depth,
                    s['total_trades'], s['win_rate'], s['profit_factor'],
                    s['max_drawdown_pct'], tp_hits, sl_hits, fc))

results.sort(key=lambda x: -x[0])
print("\n=== TOP 5 BY PF ===")
for pf, s, tp_rr, partial_rr_mode, stop_buf, depth, tp_hits, sl_hits, fc in results[:5]:
    print('tp={} p={} sb={} d={}: n={:3d} WR={:.1%} PF={:.3f} DD={:.2f}%'.format(
        tp_rr, partial_rr_mode[:4], stop_buf, depth,
        s['total_trades'], s['win_rate'], s['profit_factor'], s['max_drawdown_pct']))
