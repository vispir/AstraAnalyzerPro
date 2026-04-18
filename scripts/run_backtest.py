"""

Backtest runner script.



Usage:

    python scripts/run_backtest.py --start 2020-01-01 --end 2024-12-31 --mode proxy

    python scripts/run_backtest.py --start 2022-01-01 --end 2024-01-01 --mode llm



    # One full backtest per downloaded pair (separate JSON each):

    python scripts/run_backtest.py --all-symbols --strategy breakout_retest_v1 --mode proxy



Modes:

    proxy  — deterministic LLM proxy (fast, free, reproducible)

    llm    — real Gemini calls (slow, ~$13 for 5yr, use for validation)



Output: prints BacktestResult.summary() + optional Monte Carlo.

         saves results to backtest_results/<timestamp>_<strategy>_<mode>_<PRIMARY>_<start>_<end>.json

"""



from __future__ import annotations



import argparse

import logging

import json

import os

import sys

from datetime import datetime



import pandas as pd



# Ensure project root is on the path when run as `python scripts/run_backtest.py`

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))



logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

logger = logging.getLogger(__name__)





def _parse_cross_symbols_arg(raw: str | None) -> tuple[str, ...] | None:

    if raw is None:

        return None

    s = raw.strip()

    if not s or s.lower() == "none":

        return ()

    return tuple(x.strip().upper() for x in s.split(",") if x.strip())





def _parse_universe_arg(raw: str | None) -> tuple[str, ...]:

    if not raw or not raw.strip():

        from astra_v2 import config



        return tuple(config.BACKTEST_MULTI_SYMBOL_UNIVERSE)

    return tuple(x.strip().upper() for x in raw.split(",") if x.strip())





def _load_primary_m15(

    primary: str,

    start: str,

    end: str,

    *,

    load_bars_xau,

    load_timeframe,

) -> pd.DataFrame:

    if primary == "XAUUSD":

        return load_bars_xau(start=start, end=end)

    return load_timeframe("M15", start=start, end=end, symbol=primary)





def _load_cross_m15_dict(

    cross_syms: tuple[str, ...],

    primary: str,

    start: str,

    end: str,

    *,

    load_timeframe,

) -> dict[str, pd.DataFrame]:

    cross_m15: dict[str, pd.DataFrame] = {}

    for sym in cross_syms:

        su = sym.strip().upper()

        if su == primary:

            continue

        try:

            cross_m15[su] = load_timeframe("M15", start=start, end=end, symbol=su)

            logger.info(f"Cross M15 {su}: {len(cross_m15[su]):,} bars")

        except FileNotFoundError as e:

            logger.warning(f"Cross symbol {su} skipped: {e}")

    return cross_m15





def _run_and_save_one_symbol(

    primary: str,

    cross_syms: tuple[str, ...],

    *,

    args: argparse.Namespace,

    strategy,

    fred_df: pd.DataFrame,

    yfinance_df: pd.DataFrame,

    cot_df: pd.DataFrame,

    run_ts: str,

    load_bars_xau,

    load_timeframe,

    run_backtest,

    run_monte_carlo,

    config,

) -> str:

    logger.info(

        f"Backtest [{primary}]: {args.start} to {args.end}, mode={args.mode}, "

        f"strategy={args.strategy}, cross={cross_syms or '—'}"

    )



    bars = _load_primary_m15(primary, args.start, args.end, load_bars_xau=load_bars_xau, load_timeframe=load_timeframe)

    logger.info(f"Primary M15 {primary}: {len(bars):,} bars")



    cross_m15 = _load_cross_m15_dict(cross_syms, primary, args.start, args.end, load_timeframe=load_timeframe)



    m1_bars = None

    h4_bars = None

    if "M1" in getattr(strategy, "required_timeframes", ()):

        logger.info(f"Loading M1 ({primary})...")

        m1_bars = load_timeframe("M1", start=args.start, end=args.end, symbol=primary)

        logger.info(f"M1 bars: {len(m1_bars):,}")

    if "H4" in getattr(strategy, "required_timeframes", ()):

        logger.info(f"Loading H4 ({primary})...")

        h4_bars = load_timeframe("H4", start=args.start, end=args.end, symbol=primary)

        logger.info(f"H4 bars: {len(h4_bars):,}")



    result = run_backtest(

        bars=bars,

        fred_df=fred_df,

        yfinance_df=yfinance_df,

        cot_df=cot_df,

        m1_bars=m1_bars,

        h4_bars=h4_bars,

        mode=args.mode,

        strategy_id=args.strategy,

        start_balance=args.balance,

        wf_train_months=args.train_months,

        wf_test_months=args.test_months,

        primary_symbol=primary,

        cross_symbol_m15=cross_m15 if cross_m15 else None,

    )



    summary = result.summary()

    print(f"\n=== BACKTEST RESULTS [{primary}] ===")

    print(json.dumps(summary, indent=2))



    print(f"\n=== PROP FIRM VALIDATION [{primary}] ===")

    pf_ok = summary["profit_factor"] >= 1.5

    dd_ok = summary["max_drawdown_pct"] <= 5.0

    print(f"Profit Factor >= 1.5:  {'PASS' if pf_ok else 'FAIL'} ({summary['profit_factor']:.3f})")

    print(f"Max DD <= 5%:          {'PASS' if dd_ok else 'FAIL'} ({summary['max_drawdown_pct']:.2f}%)")



    mc_summary = None

    if args.monte_carlo:

        pnl_list = [t.dollar_pnl for t in result.trades if t.status != "open"]

        if len(pnl_list) >= 10:

            logger.info("Running Monte Carlo (10,000 runs)...")

            mc = run_monte_carlo(pnl_list, start_balance=args.balance)

            mc_summary = mc.summary()

            print(f"\n=== MONTE CARLO [{primary}] ===")

            print(json.dumps(mc_summary, indent=2))

        else:

            logger.warning("Not enough trades for Monte Carlo")



    os.makedirs(args.output_dir, exist_ok=True)

    out_path = os.path.join(

        args.output_dir,

        f"{run_ts}_{args.strategy}_{args.mode}_{primary}_{args.start}_{args.end}.json",

    )



    trades_data = [

        {

            "direction": t.direction,

            "entry": t.entry,

            "stop_loss": t.stop_loss,

            "take_profit": t.take_profit,

            "partial_tp": t.partial_tp,

            "opened_at": t.opened_at.isoformat() if t.opened_at else None,

            "closed_at": t.closed_at.isoformat() if t.closed_at else None,

            "signal_at": t.signal_at.isoformat() if t.signal_at else None,

            "signal_price": t.signal_price,

            "exit_price": t.exit_price,

            "pnl": t.pnl,

            "dollar_pnl": t.dollar_pnl,

            "status": t.status,

            "strategy_id": t.strategy_id,

            "setup_family": t.setup_family,

            "session_label": t.session_label,

            "sweep_side": t.sweep_side,

            "sweep_size": t.sweep_size,

            "confirmation_at": t.confirmation_at.isoformat() if t.confirmation_at else None,

            "confirmation_type": t.confirmation_type,

            "bars_since_sweep": t.bars_since_sweep,

            "execution_timeframe": t.execution_timeframe,

            "entry_trigger_price": t.entry_trigger_price,

            "intraday_forced_exit": t.intraday_forced_exit,

            "opening_bar_behavior": t.opening_bar_behavior,

            "first_excursion_side": t.first_excursion_side,

            "bars_to_first_profit": t.bars_to_first_profit,

            "bars_to_first_drawdown": t.bars_to_first_drawdown,

            "max_favorable_excursion_usd": t.max_favorable_excursion_usd,

            "max_adverse_excursion_usd": t.max_adverse_excursion_usd,

            "level_type": t.level_type,

            "level_direction": t.level_direction,

            "level_price": t.level_price,

            "level_strength": t.level_strength,

            "macro_direction": t.macro_direction,

            "macro_confidence": t.macro_confidence,

            "macro_reasoning": t.macro_reasoning,

        }

        for t in result.trades

        if t.status != "open"

    ]



    output = {

        "run_at": datetime.now().isoformat(),

        "params": {

            "start": args.start,

            "end": args.end,

            "mode": args.mode,

            "strategy": args.strategy,

            "primary_symbol": primary,

            "cross_symbols": sorted(cross_m15.keys()) if cross_m15 else [],

            "cross_asset_mode": getattr(config, "CROSS_ASSET_MODE", "none"),

            "balance": args.balance,

            "train_months": args.train_months,

            "test_months": args.test_months,

            "cache_only_macro": args.cache_only_macro,

            "refresh_yfinance": args.refresh_yfinance,

            "refresh_cot": args.refresh_cot,

            "all_symbols_run": bool(getattr(args, "all_symbols", False)),

        },

        "summary": summary,

        "data_status": {

            "fred_rows": len(fred_df),

            "yfinance_rows": len(yfinance_df),

            "cot_rows": len(cot_df),

            "m1_rows": len(m1_bars) if m1_bars is not None else 0,

            "h4_rows": len(h4_bars) if h4_bars is not None else 0,

            "yfinance_available": bool(not yfinance_df.empty),

            "cot_available": bool(not cot_df.empty),

            "m1_available": bool(m1_bars is not None and not m1_bars.empty),

            "h4_available": bool(h4_bars is not None and not h4_bars.empty),

        },

        "prop_firm": {"profit_factor_pass": bool(pf_ok), "max_drawdown_pass": bool(dd_ok)},

        "monte_carlo": mc_summary,

        "trades": trades_data,

        "equity_curve": result.equity_curve,

    }



    with open(out_path, "w", encoding="utf-8") as f:

        json.dump(output, f, indent=2)



    logger.info(f"Results saved to {out_path}")

    return out_path





def main():

    parser = argparse.ArgumentParser(description="Astra v2 Backtester")

    parser.add_argument("--start", default="2020-01-01", help="Start date")

    parser.add_argument("--end", default="2024-12-31", help="End date")

    parser.add_argument("--mode", choices=["proxy", "llm"], default="proxy")

    parser.add_argument("--strategy", default="legacy_v1", help="Strategy ID")

    parser.add_argument(

        "--primary-symbol",

        default=None,

        help="Primary M15 symbol when not using --all-symbols (default: BACKTEST_PRIMARY_SYMBOL)",

    )

    parser.add_argument(

        "--cross-symbols",

        default=None,

        help='Extra M15 symbols for context (default: BACKTEST_CROSS_SYMBOLS). Use "none" to disable.',

    )

    parser.add_argument(

        "--all-symbols",

        action="store_true",

        help="Run a separate full backtest for each symbol in --symbols (default universe: all downloaded FX/crypto M15)",

    )

    parser.add_argument(

        "--symbols",

        default=None,

        help="Comma list for --all-symbols (default: BACKTEST_MULTI_SYMBOL_UNIVERSE). Example: XAUUSD,BTCUSD,XAGUSD,EURUSD",

    )

    parser.add_argument("--balance", type=float, default=10_000.0, help="Starting balance USD")

    parser.add_argument("--train-months", type=int, default=6)

    parser.add_argument("--test-months", type=int, default=1)

    parser.add_argument("--monte-carlo", action="store_true", help="Run Monte Carlo simulation")

    parser.add_argument("--cache-only-macro", action="store_true", help="Use only local yfinance/COT cache")

    parser.add_argument("--refresh-yfinance", action="store_true", help="Refresh yfinance cache from network")

    parser.add_argument("--refresh-cot", action="store_true", help="Refresh COT cache from network")

    parser.add_argument("--output-dir", default="backtest_results", help="Directory to save results")

    args = parser.parse_args()



    from astra_v2 import config

    from astra_v2.data.dukascopy import load as load_bars_xau, load_timeframe

    from astra_v2.data.fred_client import fetch_all as fetch_fred_bulk

    from astra_v2.data.external import fetch_yfinance_bulk, fetch_cot_gold

    from astra_v2.backtest.engine import run_backtest

    from astra_v2.backtest.monte_carlo import run_monte_carlo

    from astra_v2.strategies import get_strategy



    if args.all_symbols and args.primary_symbol:

        logger.warning("--all-symbols set: ignoring --primary-symbol")



    strategy = get_strategy(args.strategy)



    logger.info("Loading FRED bulk data...")

    fred_df = fetch_fred_bulk(args.start, args.end)



    logger.info("Loading yfinance bulk data...")

    yfinance_df = fetch_yfinance_bulk(

        args.start,

        args.end,

        force_refresh=args.refresh_yfinance,

        cache_only=args.cache_only_macro,

    )



    logger.info("Loading COT data...")

    cot_df = fetch_cot_gold(

        force_refresh=args.refresh_cot,

        cache_only=args.cache_only_macro,

    )



    run_ts = datetime.now().strftime("%Y%m%d_%H%M%S")



    if args.all_symbols:

        universe = _parse_universe_arg(args.symbols)

        logger.info(f"--all-symbols: universe = {universe}")

        for primary in universe:

            cross_syms = tuple(s for s in universe if s != primary)

            _run_and_save_one_symbol(

                primary,

                cross_syms,

                args=args,

                strategy=strategy,

                fred_df=fred_df,

                yfinance_df=yfinance_df,

                cot_df=cot_df,

                run_ts=run_ts,

                load_bars_xau=load_bars_xau,

                load_timeframe=load_timeframe,

                run_backtest=run_backtest,

                run_monte_carlo=run_monte_carlo,

                config=config,

            )

        return



    primary = (args.primary_symbol or config.BACKTEST_PRIMARY_SYMBOL).strip().upper()

    cross_syms = _parse_cross_symbols_arg(args.cross_symbols)

    if cross_syms is None:

        cross_syms = tuple(config.BACKTEST_CROSS_SYMBOLS)



    _run_and_save_one_symbol(

        primary,

        cross_syms,

        args=args,

        strategy=strategy,

        fred_df=fred_df,

        yfinance_df=yfinance_df,

        cot_df=cot_df,

        run_ts=run_ts,

        load_bars_xau=load_bars_xau,

        load_timeframe=load_timeframe,

        run_backtest=run_backtest,

        run_monte_carlo=run_monte_carlo,

        config=config,

    )





if __name__ == "__main__":

    main()


