import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
from pipeline.fetch import fetch_fyers_ltp, fetch_yfinance_ltp, fetch_ltp_with_fallback, LTPQuote, FyersAuthError


def test_fetch_fyers_ltp_returns_dict_of_quotes():
    fake_response = {
        "s": "ok",
        "d": [
            {"n": "NSE:RELIANCE-EQ", "v": {"lp": 2450.10, "tt": 1720000000}},
            {"n": "NSE:TCS-EQ",       "v": {"lp": 3820.50, "tt": 1720000000}},
        ],
    }
    with patch("pipeline.fetch._fyers_client") as mock_client:
        mock_client.return_value.quotes.return_value = fake_response
        result = fetch_fyers_ltp(["RELIANCE", "TCS"])
    assert set(result.keys()) == {"RELIANCE", "TCS"}
    assert isinstance(result["RELIANCE"], LTPQuote)
    assert result["RELIANCE"].ltp == 2450.10
    assert result["RELIANCE"].source == "fyers"


def test_fetch_fyers_ltp_raises_on_auth_failure():
    with patch("pipeline.fetch._fyers_client") as mock_client:
        mock_client.return_value.quotes.return_value = {"s": "error", "code": -300, "message": "invalid access token"}
        with pytest.raises(FyersAuthError):
            fetch_fyers_ltp(["RELIANCE"])


def test_fetch_yfinance_ltp_returns_dict():
    import pandas as pd
    fake_hist = pd.DataFrame(
        {"Close": [2450.5]},
        index=pd.DatetimeIndex(["2026-07-15 10:30"], tz="UTC"),
    )
    with patch("pipeline.fetch.yf.download") as mock_dl:
        mock_dl.return_value = fake_hist
        result = fetch_yfinance_ltp(["RELIANCE"])
    assert "RELIANCE" in result
    assert result["RELIANCE"].source == "yfinance"


def test_fetch_ltp_with_fallback_prefers_fyers():
    fyers_quote = {"RELIANCE": LTPQuote("RELIANCE", 2450.10, datetime.now(timezone.utc), "fyers")}
    with patch("pipeline.fetch.fetch_fyers_ltp", return_value=fyers_quote):
        result, source = fetch_ltp_with_fallback(["RELIANCE"])
    assert source == "fyers"
    assert result["RELIANCE"].ltp == 2450.10


def test_fetch_ltp_with_fallback_uses_yfinance_on_fyers_auth_fail():
    yf_quote = {"RELIANCE": LTPQuote("RELIANCE", 2451.00, datetime.now(timezone.utc), "yfinance")}
    with patch("pipeline.fetch.fetch_fyers_ltp", side_effect=FyersAuthError("bad token")):
        with patch("pipeline.fetch.fetch_yfinance_ltp", return_value=yf_quote):
            result, source = fetch_ltp_with_fallback(["RELIANCE"])
    assert source == "yfinance"
    assert result["RELIANCE"].source == "yfinance"
