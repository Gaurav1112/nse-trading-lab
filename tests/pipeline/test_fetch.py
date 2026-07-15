import pytest
from unittest.mock import patch, MagicMock
from pipeline.fetch import fetch_fyers_ltp, LTPQuote, FyersAuthError


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
