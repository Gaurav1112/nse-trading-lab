import os
import pandas as pd
import pytest

from nse_backtest import data as data_mod


def test_validate_symbol_accepts_normal():
    assert data_mod._validate_symbol("RELIANCE") == "RELIANCE"
    assert data_mod._validate_symbol("M&M") == "M&M"
    assert data_mod._validate_symbol("BAJAJ-AUTO") == "BAJAJ-AUTO"
    assert data_mod._validate_symbol("^NSEI") == "^NSEI"


def test_validate_symbol_rejects_traversal():
    bad_inputs = ("../etc/passwd", "RELIANCE; rm -rf /", "RELIANCE/../X",
                  "RELIANCE`whoami`", "RELIANCE$(id)", " ", "")
    for bad in bad_inputs:
        with pytest.raises(ValueError):
            data_mod._validate_symbol(bad)


def test_cache_path_within_cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(data_mod, "CACHE_DIR", str(tmp_path))
    p = data_mod._cache_path("RELIANCE", "NSE", "2020-01-01", "2024-01-01")
    real = os.path.realpath(p)
    assert real.startswith(os.path.realpath(str(tmp_path)))


def test_atomic_write_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(data_mod, "CACHE_DIR", str(tmp_path))
    df = pd.DataFrame({"a": [1, 2, 3]})
    target = str(tmp_path / "x.parquet")
    data_mod._atomic_write_parquet(df, target)
    assert os.path.exists(target)
    out = pd.read_parquet(target)
    assert list(out["a"]) == [1, 2, 3]
