"""
Download Dukascopy XAUUSD M15 data from 2026-04-18 to 2026-05-04,
merge with existing 2020-2026-04-18 cache, run LONG+SHORT combined backtest.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import struct
import lzma
import requests
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

CACHE_DIR = Path(__file__).parent.parent / "data_cache" / "dukascopy" / "m15" / "XAUUSD"
OLD_FILE  = CACHE_DIR / "xauusd_m15_2020-01-01_2026-04-18.parquet"
NEW_FILE  = CACHE_DIR / "xauusd_m15_2020-01-01_2026-05-04.parquet"

DUKA_BASE = "https://datafeed.dukascopy.com/datafeed/XAUUSD"

DOWNLOAD_START = datetime(2026, 4, 18, tzinfo=timezone.utc)
DOWNLOAD_END   = datetime(2026, 5,  4, tzinfo=timezone.utc)


def bi5_url(dt):
    return f"{DUKA_BASE}/{dt.year:04d}/{(dt.month-1):02d}/{dt.day:02d}/{dt.hour:02d}h_ticks.bi5"


def parse_bi5(raw, hour_dt):
    try:
        data = lzma.decompress(raw)
    except Exception:
        return []
    record_size = 20
    ticks = []
    for i in range(0, len(data) - record_size + 1, record_size):
        chunk = data[i:i+record_size]
        if len(chunk) < record_size:
            break
        ts_ms, ask, bid, vol_ask, vol_bid = struct.unpack(">IIIff", chunk)
        ts  = hour_dt + timedelta(milliseconds=ts_ms)
        mid = (ask + bid) / 2 / 1000.0
        ticks.append({"timestamp": ts, "price": mid, "volume": vol_ask + vol_bid})
    return ticks


def fetch_hour(dt):
    url = bi5_url(dt)
    try:
        s = requests.Session()
        s.headers["User-Agent"] = "Mozilla/5.0"
        r = s.get(url, timeout=15)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        ticks = parse_bi5(r.content, dt)
        if not ticks:
            return None
        df = pd.DataFrame(ticks).set_index("timestamp")
        df.index = pd.to_datetime(df.index, utc=True)
        ohlcv = df["price"].resample("15min").ohlc()
        ohlcv["volume"] = df["volume"].resample("15min").sum()
        return ohlcv.dropna()
    except Exception as e:
        print(f"  WARN: {url} -> {e}")
        return None


def download_range(start_dt, end_dt):
    hours = []
    cur = start_dt
    while cur < end_dt:
        wd = cur.weekday()
        if not (wd == 5 or (wd == 6 and cur.hour < 21)):
            hours.append(cur)
        cur += timedelta(hours=1)

    print(f"  Fetching {len(hours)} trading hours ({start_dt.date()} → {end_dt.date()})...")
    frames = []
    with ThreadPoolExecutor(max_workers=16) as pool:
        futs = {pool.submit(fetch_hour, dt): dt for dt in hours}
        done = 0
        for fut in as_completed(futs):
            done += 1
            df = fut.result()
            if df is not None and len(df) > 0:
                frames.append(df)
    if not frames:
        raise RuntimeError("No data downloaded")
    result = pd.concat(frames).sort_index()
    result = result[~result.index.duplicated(keep="first")]
    result = result[(result["volume"] > 0) & (result["high"] >= result["low"])]
    print(f"  Downloaded {len(result)} new M15 bars")
    return result


def main():
    print("=" * 60)
    print("STEP 1: Download new data 2026-04-18 to 2026-05-04")
    print("=" * 60)

    new_data = download_range(DOWNLOAD_START, DOWNLOAD_END)

    print("\nSTEP 2: Load existing cache and merge")
    old_data = pd.read_parquet(OLD_FILE)
    old_data.index = pd.to_datetime(old_data.index, utc=True)
    print(f"  Old data: {old_data.index[0].date()} → {old_data.index[-1].date()} ({len(old_data):,} bars)")

    merged = pd.concat([old_data, new_data]).sort_index()
    merged = merged[~merged.index.duplicated(keep="last")]
    print(f"  Merged:   {merged.index[0].date()} → {merged.index[-1].date()} ({len(merged):,} bars)")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(NEW_FILE)
    print(f"  Saved: {NEW_FILE}")

    print("\nSTEP 3: Run LONG+SHORT combined backtest")
    print("=" * 60)

    # Load strategy modules
    import importlib.util
    _sp = Path(__file__).parent.parent / "astra_v2" / "strategies" / "session_long_nolookahead_v1.py"
    spec = importlib.util.spec_from_file_location("strat", _sp)
    strat = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(strat)

    _vp = Path(__file__).parent.parent / "current_strategy" / "validate_combined_fb_short.py"
    vspec = importlib.util.spec_from_file_location("vmod", _vp)
    vmod = importlib.util.module_from_spec(vspec)
    vspec.loader.exec_module(vmod)

    # Patch vmod to use our merged data
    df = merged.copy()
    df['atr'] = strat._atr(df, strat.ATR_PERIOD)
    df_h4 = df.resample('4h').agg({'open':'first','high':'max','low':'min','close':'last'}).dropna()
    df_h4['ema20'] = strat._ema(df_h4, strat.H4_EMA_PERIOD)
    h4_times = df_h4.index.asi8
    h4_ema20 = df_h4['ema20'].to_numpy()

    print(f"Data: {df.index[0].date()} -- {df.index[-1].date()}  ({len(df):,} bars)\n")

    r = vmod.run(df, h4_times, h4_ema20)

    tl = r['trades_l']; ts = r['trades_s']
    n_l  = len(tl); n_s  = len(ts)
    wr_l = (tl['pnl'] > 0).sum() / n_l if n_l else 0
    wr_s = (ts['pnl'] > 0).sum() / n_s if n_s else 0
    pnl_l = tl['pnl'].sum() if n_l else 0
    pnl_s = ts['pnl'].sum() if n_s else 0

    print(f"{'':30}  {'N':>5}  {'WR':>6}  {'PnL':>9}")
    print(f"  {'LONG (asian+london+ny)':<28}  {n_l:5d}  {wr_l:.1%}  ${pnl_l:>8,.0f}")
    print(f"  {'SHORT (london_fb+ny_fb)':<28}  {n_s:5d}  {wr_s:.1%}  ${pnl_s:>8,.0f}")
    print(f"  {'':-<28}  {'-----'}  {'------'}  {'---------'}")
    print(f"  {'COMBINED':<28}  {n_l+n_s:5d}         ${r['pnl']:>8,.0f}")
    print()
    print(f"  MaxDD      = {r['max_dd']:.2f}%   {'OK' if r['max_dd'] < 10 else 'FAIL (>10%)'}")
    print(f"  MaxDailyDD = {r['max_dly_dd']:.2f}%   {'OK' if r['max_dly_dd'] < 5 else 'FAIL (>5%)'}")
    print(f"  AllYrs     = {'YES' if r['all_pos'] else 'NO'}")
    print()

    yl = r['yearly_l']; ys = r['yearly_s']; yc = r['yearly']
    all_years = sorted(yc.index)
    print(f"  {'Year':<6}  {'LONG':>8}  {'SHORT':>8}  {'COMBINED':>10}  {'OK?':>4}")
    print("  " + "-" * 46)
    for y in all_years:
        vl = yl.get(y, 0); vs = ys.get(y, 0); vc = yc.get(y, 0)
        ok = 'OK' if vc > 0 else 'LOSS'
        print(f"  {y:<6}  ${vl:>7,.0f}  ${vs:>7,.0f}  ${vc:>9,.0f}  {ok:>4}")
    print()

    if n_s > 0:
        print("  SHORT by session:")
        for sn in vmod.FB_SESSIONS:
            sub = ts[ts['session'] == sn] if 'session' in ts.columns else pd.DataFrame()
            if len(sub) == 0: continue
            ns2 = len(sub); ws2 = (sub['pnl'] > 0).sum() / ns2; ps2 = sub['pnl'].sum()
            print(f"    {sn:<14}  N={ns2:4d}  WR={ws2:.1%}  PnL=${ps2:>7,.0f}")
    print()
    print("=" * 60)
    verdict = ('PASS' if r['max_dd'] < 10 and r['max_dly_dd'] < 5 and r['all_pos'] else 'FAIL')
    print(f"  VERDICT: {verdict}")
    print("=" * 60)


if __name__ == "__main__":
    main()
