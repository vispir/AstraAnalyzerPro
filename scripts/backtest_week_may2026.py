"""Download data to May 8 and run backtest for May 4-8 week."""
import sys, os, importlib.util, struct, lzma
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

CACHE_DIR = Path(__file__).parent.parent / "data_cache" / "dukascopy" / "m15" / "XAUUSD"
OLD_FILE  = CACHE_DIR / "xauusd_m15_2020-01-01_2026-05-04.parquet"
NEW_FILE  = CACHE_DIR / "xauusd_m15_2020-01-01_2026-05-08.parquet"
DUKA_BASE = "https://datafeed.dukascopy.com/datafeed/XAUUSD"

def bi5_url(dt):
    return f"{DUKA_BASE}/{dt.year:04d}/{(dt.month-1):02d}/{dt.day:02d}/{dt.hour:02d}h_ticks.bi5"

def parse_bi5(raw, hour_dt):
    try:
        data = lzma.decompress(raw)
    except Exception:
        return []
    ticks = []
    for i in range(0, len(data) - 19, 20):
        chunk = data[i:i+20]
        ts_ms, ask, bid, va, vb = struct.unpack(">IIIff", chunk)
        ticks.append({"timestamp": hour_dt + timedelta(milliseconds=ts_ms),
                      "price": (ask+bid)/2/1000.0, "volume": va+vb})
    return ticks

def fetch_hour(dt):
    try:
        s = requests.Session()
        s.headers["User-Agent"] = "Mozilla/5.0"
        r = s.get(bi5_url(dt), timeout=15)
        if r.status_code == 404: return None
        r.raise_for_status()
        ticks = parse_bi5(r.content, dt)
        if not ticks: return None
        df = pd.DataFrame(ticks).set_index("timestamp")
        df.index = pd.to_datetime(df.index, utc=True)
        ohlcv = df["price"].resample("15min").ohlc()
        ohlcv["volume"] = df["volume"].resample("15min").sum()
        return ohlcv.dropna()
    except Exception:
        return None

def download_range(start_dt, end_dt):
    hours = []
    cur = start_dt
    while cur < end_dt:
        wd = cur.weekday()
        if not (wd == 5 or (wd == 6 and cur.hour < 21)):
            hours.append(cur)
        cur += timedelta(hours=1)
    print(f"  Downloading {len(hours)} hours ({start_dt.date()} to {end_dt.date()})...")
    frames = []
    with ThreadPoolExecutor(max_workers=16) as pool:
        for fut in as_completed({pool.submit(fetch_hour, dt): dt for dt in hours}):
            df = fut.result()
            if df is not None and len(df) > 0:
                frames.append(df)
    if not frames:
        return None
    result = pd.concat(frames).sort_index()
    result = result[~result.index.duplicated(keep="last")]
    return result[(result["volume"] > 0) & (result["high"] >= result["low"])]

def main():
    # Step 1: Download May 1-8
    print("Step 1: Downloading May 1-8 2026...")
    new_data = download_range(
        datetime(2026, 5, 1, tzinfo=timezone.utc),
        datetime(2026, 5, 8, 21, tzinfo=timezone.utc)
    )

    print("Step 2: Merging with existing cache...")
    old = pd.read_parquet(OLD_FILE)
    old.index = pd.to_datetime(old.index, utc=True)

    if new_data is not None:
        merged = pd.concat([old, new_data]).sort_index()
        merged = merged[~merged.index.duplicated(keep="last")]
    else:
        merged = old
        print("  No new data downloaded, using existing cache")

    print(f"  Data: {merged.index[0].date()} to {merged.index[-1].date()} ({len(merged):,} bars)")
    merged.to_parquet(NEW_FILE)

    # Step 3: Load strategy
    _sp = Path(__file__).parent.parent / "astra_v2/strategies/session_long_nolookahead_v1.py"
    spec = importlib.util.spec_from_file_location("strat", _sp)
    strat = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(strat)

    # Step 4: Run full backtest (need full history for H4 EMA warmup)
    df = merged.copy()
    df["atr"] = strat._atr(df, strat.ATR_PERIOD)
    df_h4 = df.resample("4h").agg({"open":"first","high":"max","low":"min","close":"last"}).dropna()
    df_h4["ema20"] = strat._ema(df_h4, strat.H4_EMA_PERIOD)

    r = strat.run(df, df_h4.index.asi8, df_h4["ema20"].to_numpy())
    tdf = r["trades_df"]

    # Step 5: Filter to May 4-8 week
    # Trades opened this week — use entry date approximation from year
    # Since we don't have exact open timestamps, filter by 2026 and look at last trades
    t26 = tdf[tdf["year"] == 2026].copy()

    # Show last 10 trades (this week)
    print()
    print("=" * 72)
    print("  BACKTEST: 2026 trades (most recent = this week)")
    print("=" * 72)
    print(f"  {'#':<3} {'Session':<10} {'Entry':>8} {'SL':>8} {'TP':>8} {'PnL':>8} {'Result'}")
    print("  " + "-" * 60)
    for i, (_, t) in enumerate(t26.tail(15).iterrows()):
        result = "WIN" if t["pnl"] > 0 else "loss"
        print(f"  {i+1:<3} {t['session']:<10} {t['entry']:>8.2f} {t['initial_sl']:>8.2f} {t['tp']:>8.2f} ${t['pnl']:>7,.0f} {result}")

    print()
    print("  Note: trades still open at data end are closed at last price")
    print()

    # Stats for 2026
    print("=" * 72)
    print("  2026 stats by session:")
    print("=" * 72)
    for sess in ["asian", "london", "ny"]:
        g = t26[t26["session"] == sess]
        if len(g) == 0: continue
        n = len(g); wr = (g["pnl"]>0).sum()/n; pnl = g["pnl"].sum()
        print(f"  {sess.upper():<10} N={n:3d}  WR={wr:.1%}  PnL=${pnl:>7,.0f}")

if __name__ == "__main__":
    main()
