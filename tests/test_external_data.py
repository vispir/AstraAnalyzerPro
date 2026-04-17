from __future__ import annotations

import pandas as pd
import shutil
import uuid
from pathlib import Path

from astra_v2.data.external import fetch_cot_gold, fetch_yfinance_bulk


def _workspace_tmp_dir() -> Path:
    path = Path("data_cache") / f"test_external_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_fetch_yfinance_bulk_uses_cache_when_coverage_exists(monkeypatch):
    tmp_dir = _workspace_tmp_dir()
    cache_path = tmp_dir / "yfinance.parquet"
    index = pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"])
    columns = pd.MultiIndex.from_product([["Close"], ["DX-Y.NYB", "^VIX", "^TNX"]])
    cached = pd.DataFrame(
        [
            [101.0, 12.0, 4.1],
            [102.0, 13.0, 4.2],
            [103.0, 14.0, 4.3],
        ],
        index=index,
        columns=columns,
    )
    cached.to_parquet(cache_path)

    def fail_download(*args, **kwargs):
        raise AssertionError("network should not be used when cache covers the range")

    monkeypatch.setattr("astra_v2.data.external.yf.download", fail_download)

    try:
        result = fetch_yfinance_bulk("2024-01-01", "2024-01-04", cache_path=str(cache_path))
        assert len(result) == 3
        assert float(result.iloc[-1][("Close", "^VIX")]) == 14.0
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_fetch_yfinance_bulk_cache_only_returns_empty_without_cache(monkeypatch):
    tmp_dir = _workspace_tmp_dir()
    def fail_download(*args, **kwargs):
        raise AssertionError("network should not be used in cache_only mode")

    monkeypatch.setattr("astra_v2.data.external.yf.download", fail_download)

    try:
        result = fetch_yfinance_bulk(
            "2024-01-01",
            "2024-01-04",
            cache_path=str(tmp_dir / "missing.parquet"),
            cache_only=True,
        )
        assert result.empty
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_fetch_cot_gold_uses_cache_when_present(monkeypatch):
    tmp_dir = _workspace_tmp_dir()
    cache_path = tmp_dir / "cot.parquet"
    cached = pd.DataFrame(
        {"net_noncommercial": [150_000, 125_000]},
        index=pd.to_datetime(["2024-01-02", "2024-01-09"]),
    )
    cached.to_parquet(cache_path)

    def fail_get(*args, **kwargs):
        raise AssertionError("network should not be used when COT cache exists")

    monkeypatch.setattr("astra_v2.data.external.requests.get", fail_get)

    try:
        result = fetch_cot_gold(cache_path=str(cache_path))
        assert len(result) == 2
        assert int(result.iloc[0]["net_noncommercial"]) == 150_000
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_fetch_cot_gold_cache_only_returns_empty_without_cache(monkeypatch):
    tmp_dir = _workspace_tmp_dir()
    def fail_get(*args, **kwargs):
        raise AssertionError("network should not be used in cache_only mode")

    monkeypatch.setattr("astra_v2.data.external.requests.get", fail_get)

    try:
        result = fetch_cot_gold(cache_path=str(tmp_dir / "missing.parquet"), cache_only=True)
        assert result.empty
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
