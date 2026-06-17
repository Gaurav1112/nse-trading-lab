"""Tests for the Zerodha order/sell/modify-SL HTML card helpers."""
from __future__ import annotations

from components.cards import (
    zerodha_steps, zerodha_sell_steps, zerodha_modify_sl_steps,
)


def test_buy_steps_include_basic_order():
    out = zerodha_steps("RELIANCE", entry=1300.0, sl=1250.0, shares=10)
    assert "RELIANCE" in out
    assert "₹1,300" in out
    assert "₹1,250" in out
    assert "CNC" in out
    assert "SL-M" in out


def test_buy_steps_include_t1_t2_when_provided():
    out = zerodha_steps("HDFCBANK", entry=1500.0, sl=1450.0, shares=20,
                        target_1=1600.0, target_2=1750.0)
    assert "GTT" in out
    assert "₹1,600" in out
    assert "₹1,750" in out
    # 50% qty for T1, rest for T2
    assert "Qty <b>10</b>" in out  # half of 20
    assert "Qty <b>10</b>" in out


def test_buy_steps_omit_targets_when_zero():
    """No GTT order lines when T1/T2 not provided. The closing tip
    mentions 'GTT' in prose but no <li> for the order itself."""
    out = zerodha_steps("INFY", entry=1500.0, sl=1450.0, shares=10,
                        target_1=0, target_2=0)
    assert "GTT (Single)" not in out
    assert "₹0.00" not in out


def test_sell_steps_include_market_and_qty():
    out = zerodha_sell_steps("TCS", current_price=4000.0, shares_held=5,
                              reason="Score decay")
    assert "TCS" in out
    assert "MARKET" in out
    assert "Qty <b>5</b>" in out
    assert "Score decay" in out
    assert "SELL" in out


def test_sell_steps_no_reason_still_renders():
    out = zerodha_sell_steps("WIPRO", current_price=500.0, shares_held=20)
    assert "WIPRO" in out
    assert "MARKET" in out


def test_modify_sl_steps_show_old_and_new():
    out = zerodha_modify_sl_steps("ICICIBANK", old_sl=1000.0, new_sl=1050.0,
                                  shares_held=15)
    assert "ICICIBANK" in out
    assert "₹1,000" in out
    assert "₹1,050" in out
    assert "Modify" in out
    assert "Trigger Price" in out


def test_modify_sl_steps_compute_delta():
    out = zerodha_modify_sl_steps("HCLTECH", old_sl=1500.0, new_sl=1530.0,
                                  shares_held=10)
    # +30 INR, +2.00%
    assert "+30.00" in out or "+30 INR" in out
    assert "+2.00%" in out
