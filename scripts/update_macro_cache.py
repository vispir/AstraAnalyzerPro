"""
Populate local macro caches used by backtests and live analysis.

Usage:
    python scripts/update_macro_cache.py --start 2020-01-01 --end 2024-12-31
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Update local macro caches")
    parser.add_argument("--start", default="2020-01-01", help="Start date")
    parser.add_argument("--end", default="2024-12-31", help="End date")
    parser.add_argument("--skip-fred", action="store_true", help="Skip FRED refresh")
    parser.add_argument("--skip-yfinance", action="store_true", help="Skip yfinance refresh")
    parser.add_argument("--skip-cot", action="store_true", help="Skip COT refresh")
    args = parser.parse_args()

    from astra_v2.data.external import fetch_cot_gold, fetch_yfinance_bulk
    from astra_v2.data.fred_client import fetch_all as fetch_fred_bulk

    if not args.skip_fred:
        logger.info("Refreshing FRED cache...")
        fetch_fred_bulk(args.start, args.end, force_refresh=True)

    if not args.skip_yfinance:
        logger.info("Refreshing yfinance cache...")
        fetch_yfinance_bulk(args.start, args.end, force_refresh=True)

    if not args.skip_cot:
        logger.info("Refreshing COT cache...")
        fetch_cot_gold(force_refresh=True)

    logger.info("Macro cache update complete")


if __name__ == "__main__":
    main()
