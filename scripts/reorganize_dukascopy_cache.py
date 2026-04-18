"""
Move existing Dukascopy parquets into layout:
  <DUKASCOPY_CACHE_DIR>/m1/<PAIR>/chunks/   — M1 yearly chunks
  <DUKASCOPY_CACHE_DIR>/m1/<PAIR>/         — merged M1 (optional)
  <DUKASCOPY_CACHE_DIR>/m15/<PAIR>/        — M15
  <DUKASCOPY_CACHE_DIR>/h4/<PAIR>/        — H4

Run once after upgrading path layout. Safe to re-run (skips if destination exists).
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from astra_v2 import config
from astra_v2.data.dukascopy import dukascopy_m1_chunks_dir, dukascopy_ohlcv_path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

_FLAT_OHLCV = re.compile(
    r"^([a-z]+)_(m1|m15|h4)_(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})\.parquet$",
    re.IGNORECASE,
)


def _move_if_needed(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.resolve() == src.resolve():
        return
    if dest.exists():
        logger.info(f"Skip (exists): {dest}")
        return
    shutil.move(str(src), str(dest))
    logger.info(f"Moved -> {dest}")


def main() -> None:
    base = Path(config.DUKASCOPY_CACHE_DIR)
    if not base.is_dir():
        logger.warning(f"Cache dir missing: {base}")
        return

    for src in sorted(base.glob("*.parquet")):
        m = _FLAT_OHLCV.match(src.name)
        if not m:
            continue
        sym_l, tf, start, end = m.group(1).lower(), m.group(2).lower(), m.group(3), m.group(4)
        sym_u = sym_l.upper()
        dest = dukascopy_ohlcv_path(base, sym_u, tf.upper(), start, end)
        _move_if_needed(src, dest)

    legacy_chunks = base / "_m1_chunks"
    if not legacy_chunks.is_dir():
        logger.info("No legacy _m1_chunks directory")
        return

    for child in sorted(legacy_chunks.iterdir()):
        if child.is_file() and child.suffix == ".parquet":
            m = re.match(
                r"^([a-z]+)_m1_(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})\.parquet$",
                child.name,
                re.I,
            )
            if not m:
                logger.warning(f"Unknown chunk file, left in place: {child}")
                continue
            sym_l, a, b = m.group(1).lower(), m.group(2), m.group(3)
            dest_dir = dukascopy_m1_chunks_dir(base, sym_l.upper())
            _move_if_needed(child, dest_dir / child.name.lower())

        elif child.is_dir() and child.name.lower() in (
            "btcusd",
            "xagusd",
            "eurusd",
            "xauusd",
        ):
            sym_u = child.name.upper()
            dest_dir = dukascopy_m1_chunks_dir(base, sym_u)
            for src in sorted(child.glob("*.parquet")):
                _move_if_needed(src, dest_dir / src.name.lower())
            try:
                child.rmdir()
            except OSError:
                pass

    try:
        legacy_chunks.rmdir()
        logger.info(f"Removed empty {legacy_chunks}")
    except OSError:
        leftover = list(legacy_chunks.iterdir())
        if leftover:
            logger.warning(f"_m1_chunks not empty ({len(leftover)} items), not removed")


if __name__ == "__main__":
    main()
