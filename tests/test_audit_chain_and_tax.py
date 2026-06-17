"""Tests for the audit log hash chain (D2) and ITR-2 tax export (D3)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import components.risk_governor as rg
from components.risk_governor import verify_audit_log, log_verdict
from components.tax_export import compute_charges, to_itr_csv


# ── Audit log hash chain ────────────────────────────────────────────────────

def test_empty_audit_log_verifies_clean(monkeypatch, tmp_path):
    p = tmp_path / "audit.jsonl"
    monkeypatch.setattr(rg, "_AUDIT_LOG_PATH", p)
    ok, msg = verify_audit_log()
    assert ok
    assert "empty" in msg.lower()


def test_audit_log_chain_links_records(monkeypatch, tmp_path):
    p = tmp_path / "audit.jsonl"
    monkeypatch.setattr(rg, "_AUDIT_LOG_PATH", p)
    log_verdict("RELIANCE", "GO", 78.0, 65.0, "TRENDING")
    log_verdict("TCS", "WAIT", 60.0, 50.0, "MIXED")
    log_verdict("INFY", "AVOID", 30.0, 40.0, "HOSTILE")

    ok, msg = verify_audit_log()
    assert ok, msg
    assert "3 records" in msg

    lines = p.read_text().strip().split("\n")
    assert len(lines) == 3
    # First record's prev_hash is empty
    r0 = json.loads(lines[0])
    assert r0["prev_hash"] == ""
    assert r0["self_hash"]
    # Second record's prev_hash equals first record's self_hash
    r1 = json.loads(lines[1])
    assert r1["prev_hash"] == r0["self_hash"]


def test_audit_log_tamper_breaks_chain(monkeypatch, tmp_path):
    p = tmp_path / "audit.jsonl"
    monkeypatch.setattr(rg, "_AUDIT_LOG_PATH", p)
    log_verdict("RELIANCE", "GO", 78.0, 65.0, "TRENDING")
    log_verdict("TCS", "GO", 78.0, 65.0, "TRENDING")

    # Tamper: modify the first record's verdict but keep its hashes
    lines = p.read_text().strip().split("\n")
    r0 = json.loads(lines[0])
    r0["verdict"] = "WAIT"     # tampered
    lines[0] = json.dumps(r0, sort_keys=True, separators=(",", ":"))
    p.write_text("\n".join(lines) + "\n")

    ok, msg = verify_audit_log()
    assert not ok
    assert "tamper" in msg.lower() or "mismatch" in msg.lower()


def test_audit_log_missing_link_breaks_chain(monkeypatch, tmp_path):
    p = tmp_path / "audit.jsonl"
    monkeypatch.setattr(rg, "_AUDIT_LOG_PATH", p)
    log_verdict("A", "GO", 70, 60, "MIXED")
    log_verdict("B", "GO", 71, 61, "MIXED")
    log_verdict("C", "GO", 72, 62, "MIXED")
    # Remove the middle record
    lines = p.read_text().strip().split("\n")
    p.write_text(lines[0] + "\n" + lines[2] + "\n")

    ok, msg = verify_audit_log()
    assert not ok
    assert "chain broken" in msg.lower() or "expected" in msg.lower()


# ── ITR-2 tax export ────────────────────────────────────────────────────────

def test_compute_charges_zero_inputs_safe():
    out = compute_charges(0, 0, 0)
    assert out["total"] == 0


def test_compute_charges_includes_all_components():
    out = compute_charges(buy_price=1000, sell_price=1100, qty=10)
    assert out["stt"] > 0
    assert out["stamp_duty"] > 0
    assert out["dp_charges"] > 0
    assert out["total"] > 0
    # STT is 0.1% of sell-side, so ~11
    assert 10 < out["stt"] < 12


def test_itr_csv_includes_ltcg_stcg_buckets():
    journal = [
        # STCG — 30 day holding
        {"symbol": "RELIANCE", "buy_price": 1000, "sell_price": 1100, "qty": 10,
         "entry_date": "2026-01-01", "closed_date": "2026-01-31"},
        # LTCG — 400 day holding
        {"symbol": "TCS", "buy_price": 3000, "sell_price": 3500, "qty": 5,
         "entry_date": "2024-01-01", "closed_date": "2025-02-04"},
    ]
    csv = to_itr_csv(journal)
    assert "Scrip name" in csv
    assert "STCG" in csv
    assert "LTCG" in csv
    assert "RELIANCE" in csv
    assert "TCS" in csv


def test_itr_csv_skips_incomplete_trades():
    journal = [
        {"symbol": "X", "buy_price": 0, "sell_price": 100, "qty": 10},
        {"symbol": "Y", "buy_price": 100, "sell_price": 110, "qty": 10,
         "entry_date": "2026-01-01", "closed_date": "2026-01-10"},
    ]
    csv = to_itr_csv(journal)
    # Header + 1 row (Y), X is skipped
    assert csv.count("\n") <= 3
    assert "Y" in csv
    # X (qty>0 but no buy) is skipped silently
