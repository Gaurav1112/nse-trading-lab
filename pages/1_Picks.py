import streamlit as st
import pandas as pd
from datetime import datetime
from components import theme, state, cards
from components.market_data import get_live_price
from nse_backtest.data import fetch_multiple, NIFTY50_SYMBOLS, NIFTY100_SYMBOLS
from nse_backtest.trading_modes import analyze_swing
from nse_backtest.sample_data import trending_stock, volatile_midcap, sideways_stock

st.set_page_config(page_title="Today's Picks | Trading Lab", page_icon="🎯", layout="wide")
st.markdown(theme.inject_css(), unsafe_allow_html=True)
state.init_session()

st.markdown("# 🎯 Today's Best Stocks")
st.markdown("_Top actionable NSE stocks right now. Scored, ranked, one-tap position entry._")

# ── Tape regime banner ─────────────────────────────────────────
from nse_backtest.tape_monitor import assess_tape, TapeRegime
from nse_backtest.data import fetch_nifty50
from components.data_freshness import check_freshness

@st.cache_data(ttl=3600)  # refresh hourly
def _cached_nifty():
    try:
        return fetch_nifty50(start="2022-01-01")
    except Exception:
        return None

@st.cache_data(ttl=3600)
def _cached_tape():
    try:
        nifty_df = _cached_nifty()
        return assess_tape(nifty_df) if nifty_df is not None else None
    except Exception:
        return None

_tape = _cached_tape()
if _tape is not None:
    st.markdown(
        f'<div style="border:1px solid {_tape.color};border-radius:14px;padding:14px 18px;'
        f'margin:8px 0 16px 0;background:#0D1526">'
        f'<span style="font-size:11px;color:#5A7390;text-transform:uppercase;letter-spacing:1px">'
        f'Tape Regime · Nifty 50 ₹{_tape.nifty_close:,.0f}</span><br>'
        f'<span style="font-size:24px;font-weight:700;color:{_tape.color}">{_tape.regime}</span>'
        f'<span style="font-size:13px;color:#7A93AA;margin-left:12px">'
        f'60d {_tape.return_60d_pct:+.1f}% · 200EMA slope {_tape.ema_200_slope_pct_20d:+.2f}%/20d'
        f'</span>'
        f'<div style="margin-top:8px;color:#C9D5E0;font-size:13px;line-height:1.4">'
        f'{_tape.recommendation}</div>'
        f'</div>', unsafe_allow_html=True)
    # Held-out 2026 OOS banner — permanent in HOSTILE so the user sees the
    # honest expectancy every single time they look at picks. Composer-style.
    if _tape.regime == TapeRegime.HOSTILE:
        st.markdown(
            '<div style="border:2px solid #FF4D4D;border-radius:14px;padding:14px 18px;'
            'margin:0 0 16px 0;background:#1a0d0d">'
            '<span style="font-size:11px;color:#FF9090;text-transform:uppercase;letter-spacing:1px">'
            'Honest evidence (held-out 2026 YTD)</span><br>'
            '<span style="font-size:18px;font-weight:700;color:#FF4D4D">'
            'Engine returned <strong>-1.61% expectancy / trade</strong> in this regime '
            'on 2026 data it has never been tuned against (64 trades, 28% win rate). '
            'v1 returned -0.96% (175 trades, 35% win rate). '
            'Save button is hard-blocked below — typing the override does not '
            'change the underlying data.</span></div>',
            unsafe_allow_html=True,
        )
else:
    st.caption("⚠️ Tape regime unavailable (Nifty data fetch failed)")
# ── end banner ─────────────────────────────────────────────────

# ── Data freshness badge (Rohan's integrity guard) ────────────
_nifty_for_freshness = _cached_nifty()
_freshness = check_freshness(_nifty_for_freshness)
st.markdown(
    f'<div style="border:1px solid {_freshness.color};border-radius:10px;padding:8px 14px;'
    f'margin:6px 0;background:#0D1526;font-size:12px;color:#C9D5E0">'
    f'<span style="color:{_freshness.color};font-weight:700">●</span> {_freshness.message}'
    f'</div>', unsafe_allow_html=True)
if _freshness.status == "STALE":
    st.warning("⚠️ Stale data detected — picks below may be misleading. Refresh the page or check yfinance availability.")
# ── end freshness ─────────────────────────────────────────────

# ── Risk envelope banner (Kavya's portfolio guard) ─────────────
from components.risk_governor import assess as _risk_assess
_risk = _risk_assess(state.get_positions(), state.get_journal(), state.get_capital(),
                     regime=_tape.regime if _tape else None)
# Portfolio kill switch — surfaced as a dedicated banner above everything else
# so the user sees "FLATTEN ALL" before considering new entries.
if _risk.flatten_all:
    st.markdown(
        '<div style="border:3px solid #FF0000;border-radius:14px;padding:18px 20px;'
        'margin:8px 0;background:#280000">'
        '<span style="font-size:14px;color:#FFA0A0;text-transform:uppercase;letter-spacing:1px">'
        'Portfolio Kill Switch</span><br>'
        '<span style="font-size:24px;font-weight:800;color:#FF0000">⛔ FLATTEN ALL POSITIONS</span>'
        f'<div style="margin-top:8px;color:#FFD0D0;font-size:14px;line-height:1.4">'
        f'{_risk.flatten_reason} New entries are blocked. Close existing positions '
        f'and review the journal before re-engaging.</div></div>',
        unsafe_allow_html=True,
    )
if not _risk.can_trade:
    st.markdown(
        f'<div style="border:1px solid #FF4D4D;border-radius:14px;padding:14px 18px;'
        f'margin:8px 0;background:#190d0d">'
        f'<span style="font-size:11px;color:#5A7390;text-transform:uppercase;letter-spacing:1px">Risk Governor</span><br>'
        f'<span style="font-size:18px;font-weight:700;color:#FF4D4D">⛔ TRADING DISABLED</span>'
        f'<div style="margin-top:6px;color:#FFD0D0;font-size:13px;line-height:1.4">'
        + " · ".join(_risk.reasons) + '</div></div>',
        unsafe_allow_html=True,
    )
else:
    st.caption(
        f"🛡️ Risk envelope: {_risk.open_positions}/{_risk.max_open_positions} positions · "
        f"aggregate book risk **{_risk.aggregate_risk_pct:.2f}%** / cap "
        f"**{_risk.aggregate_risk_cap_pct:.1f}%** ({_tape.regime if _tape else 'TRENDING'}) · "
        f"weekly P&L {_risk.weekly_pnl_pct:+.1f}% (threshold {_risk.weekly_dd_threshold_pct:.1f}%)"
    )
# ── end risk envelope ─────────────────────────────────────────

pc1, pc2, pc3 = st.columns([2, 2, 1])
scan_univ = pc1.selectbox("Universe", ["Nifty 100 (Live)", "Nifty 50 (Live)", "Demo (Instant)"], key="picks_univ")
min_score = pc2.slider("Min Score", 40, 90, 65, key="picks_min_score")
max_picks = pc3.selectbox("Max picks", [3, 4, 5], key="picks_max")

if st.button("🔍  Find Today's Best Stocks", type="primary", use_container_width=True, key="picks_scan_btn"):
    with st.spinner("Scanning market…"):
        try:
            if "Demo" in scan_univ:
                raw = {"DEMO_UP": trending_stock(), "DEMO_MID": volatile_midcap(),
                       "DEMO_FLAT": sideways_stock(), "DEMO_UP2": trending_stock()}
            elif "100" in scan_univ:
                raw = fetch_multiple(NIFTY100_SYMBOLS, start="2021-01-01")
            else:
                raw = fetch_multiple(NIFTY50_SYMBOLS, start="2021-01-01")

            picks = []
            cap, risk = state.get_capital(), state.get_risk_pct()
            # v2 engine: thread Nifty data so regime_gate + rs_vs_nifty can fire.
            nifty_for_engine = _cached_nifty() if "Demo" not in scan_univ else None
            for sym, sdf in raw.items():
                if len(sdf) < 60:
                    continue
                avg_vol = sdf["Volume"].rolling(20).mean().iloc[-1]
                if pd.notna(avg_vol) and avg_vol < 100_000:
                    continue
                try:
                    setup = analyze_swing(sdf, sym, cap, risk, nifty_df=nifty_for_engine)
                    if setup:
                        from components.risk_governor import log_verdict
                        log_verdict(
                            symbol=sym, verdict=setup.signal,
                            score=setup.score, win_probability=setup.win_probability,
                            tape_regime=_tape.regime if _tape else "UNKNOWN",
                            engine="v2",
                        )
                    if setup and setup.signal == "BUY" and setup.score >= min_score:
                        picks.append((setup.score, sym, setup))
                except Exception:
                    pass
            picks.sort(reverse=True)
            st.session_state["today_picks"] = picks[:max_picks]
            st.session_state["today_picks_scanned"] = True
        except Exception as e:
            st.error(f"Scan failed: {e}")

picks = st.session_state.get("today_picks", [])
scan_ran = st.session_state.get("today_picks_scanned", False)
if not picks:
    if scan_ran:
        # Scan ran but zero qualified — regime-aware empty state
        if _tape is not None and _tape.regime == TapeRegime.HOSTILE:
            st.warning(
                f"**0 picks today — and that's the right answer.** Tape is **HOSTILE** "
                f"({_tape.recommendation[:120]}). The v2 engine is correctly blocking "
                f"GO verdicts in this regime. Historical expectancy in HOSTILE tape "
                f"was only +0.05% per trade (essentially break-even after costs). "
                f"Come back when the Tape Monitor banner turns MIXED (be selective) "
                f"or TRENDING (trade normally)."
            )
        elif _tape is not None and _tape.regime == TapeRegime.MIXED:
            st.info(
                f"**0 picks at score ≥{min_score}.** Tape is MIXED — engine is being selective. "
                f"Try lowering the min score slider, or open the Analyze page to deep-dive specific names."
            )
        else:
            st.info(
                f"**0 picks at score ≥{min_score}.** Try lowering the slider, or scanning the wider universe."
            )
    else:
        st.info("Hit **Find Today's Best Stocks** to scan the market.")
else:
    st.markdown(f"### ✅ {len(picks)} stocks cleared the {min_score}+ score filter")
    for rank, (sc, sym, s) in enumerate(picks, 1):
        # Apply live-price ratio correction — yfinance auto_adjust=True can return
        # split-adjusted prices that diverge from the actual market price by >5%.
        if not sym.startswith("DEMO"):
            _live, _prev_close = get_live_price(sym)
            if _live and s.entry_price > 0 and abs(_live / s.entry_price - 1.0) > 0.05:
                _r = _live / s.entry_price
                s.entry_price = round(_live, 2)
                s.stop_loss = round(s.stop_loss * _r, 2)
                s.target_1 = round(s.target_1 * _r, 2)
                s.position_value = s.suggested_qty * s.entry_price
                s.max_loss = (s.entry_price - s.stop_loss) * s.suggested_qty

        clr = "#00FF87" if sc >= 65 else "#FFB800"
        st.markdown(
            f'<div style="border:1px solid {clr};border-radius:14px;padding:18px 20px;'
            f'margin:10px 0;background:#0D1526">'
            f'<span style="font-size:11px;color:#5A7390;text-transform:uppercase">#{rank} Pick</span><br>'
            f'<span style="font-size:26px;font-weight:700;color:{clr}">{sym}</span>'
            f'<span style="font-size:13px;color:#7A93AA;margin-left:12px">Score {sc:.0f}/100</span>'
            f'</div>', unsafe_allow_html=True)
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("CMP / Entry", f"₹{s.entry_price:,.0f}")
        sl_pct = (s.entry_price - s.stop_loss) / s.entry_price * 100
        m2.metric("Stop Loss", f"₹{s.stop_loss:,.0f}", f"-{sl_pct:.1f}%")
        t1_pct = (s.target_1 - s.entry_price) / s.entry_price * 100
        m3.metric("Target 1", f"₹{s.target_1:,.0f}", f"+{t1_pct:.1f}%")
        m4.metric("R:R", f"{s.risk_reward:.1f}:1")
        m5.metric("Win %", f"{s.win_probability:.0f}%")
        # Honest uncertainty annotation (Sara + Tomás — Phase E)
        # Score ±5 is the empirical noise floor we observed across the threshold
        # sweeps; entries/stops are rounded but the market moves intraday so
        # "around" is more honest than "exactly".
        st.caption(
            f"💡 **Score interpretation:** {sc:.0f} ±5 (noise floor). "
            f"Entry/SL/T1 are reference levels — slippage typically ±0.5%. "
            f"Win% is calibrated against backtest, not live."
        )
        if s.suggested_qty > 0:
            st.caption(f"Suggested: **{s.suggested_qty} shares** = ₹{s.position_value:,.0f} | Max loss ₹{s.max_loss:,.0f}")
        with st.expander("📋 Why this stock?"):
            for r in s.reasons[:8]:
                st.caption(f"• {r}")
        st.markdown(cards.zerodha_steps(sym, s.entry_price, s.stop_loss, s.suggested_qty,
                                        target_1=s.target_1, target_2=s.target_2),
                    unsafe_allow_html=True)
        with st.expander(f"✅ I bought {sym} — record position"):
            bf1, bf2, bf3 = st.columns(3)
            bought_price = bf1.number_input("Buy price ₹", value=float(s.entry_price), min_value=0.01, format="%.2f", key=f"bp_{sym}_{rank}")
            bought_qty = bf2.number_input("Shares bought", value=max(s.suggested_qty, 1), min_value=1, key=f"bq_{sym}_{rank}")
            sl_set = bf3.number_input("Your SL ₹", value=float(s.stop_loss), min_value=0.01, format="%.2f", key=f"bsl_{sym}_{rank}")
            thesis = st.text_area(
                "📝 Thesis — why are you taking this trade? (min 20 chars)",
                key=f"th_{sym}_{rank}",
                placeholder="e.g. score 78, breakout above 200EMA on 2x volume, tape MIXED, willing to risk ₹2k for ₹6k target",
                height=80,
            )
            from components.risk_governor import can_open_in_sector
            _sector_ok, _sector_reason = can_open_in_sector(state.get_positions(), sym)

            # ── Correlation-aware Kelly suggestion ────────────────────────────
            # If positions are open, compute candidate vs open-book correlations
            # and surface a recommended qty that accounts for portfolio overlap.
            from components.correlations import book_correlations
            from nse_backtest.features.kelly_sizing import kelly_size
            _open_pos = [p for p in state.get_positions() if not p.get("closed_date")]
            _kelly_hint = None
            if _open_pos and not sym.startswith("DEMO"):
                try:
                    _open_dfs = {p["symbol"]: fetch_multiple([p["symbol"]], start="2024-01-01").get(p["symbol"])
                                 for p in _open_pos if p.get("symbol")}
                    _open_dfs = {k: v for k, v in _open_dfs.items() if v is not None}
                    _cand_df = fetch_multiple([sym], start="2024-01-01").get(sym)
                    if _cand_df is not None and _open_dfs:
                        _rhos = book_correlations(_cand_df, _open_dfs)
                        if _rhos:
                            _ks = kelly_size(
                                calibrated_win_prob_pct=s.win_probability,
                                risk_reward=s.risk_reward,
                                entry_price=bought_price,
                                stop_loss=sl_set,
                                capital=state.get_capital(),
                                max_risk_pct=state.get_risk_pct(),
                                open_book_correlations=_rhos,
                            )
                            avg_rho = sum(_rhos) / len(_rhos)
                            st.caption(
                                f"📊 Open-book correlation: ρ_avg={avg_rho:+.2f} across "
                                f"{len(_rhos)} held position(s). "
                                f"Kelly suggests **{_ks.suggested_qty} shares** "
                                f"({_ks.risk_pct_of_capital:.2f}% risk after ρ-haircut) "
                                f"vs your current input of {bought_qty}."
                            )
                            _kelly_hint = _ks
                except Exception as _e:
                    pass
            # ── HOSTILE hard-block with friction override ──────────────────────
            # Held-out 2026 YTD with v2+Wave A returned -1.61% expectancy. On
            # HOSTILE tape this is the dominant outcome. We disable Save unless
            # the user types an exact override phrase so the act of trading
            # against the engine becomes deliberate, not impulsive.
            _OVERRIDE_PHRASE = "I accept -1.61% expectancy"
            _hostile_block = (_tape is not None and _tape.regime == TapeRegime.HOSTILE)
            _override_text = ""
            if _hostile_block:
                st.error(
                    f"🛑 **HOSTILE tape — Save disabled.** Held-out 2026 YTD "
                    f"showed **-1.61% expectancy per trade** on this engine in "
                    f"this regime. To save anyway, type the exact phrase below."
                )
                _override_text = st.text_input(
                    f"Type to override: `{_OVERRIDE_PHRASE}`",
                    key=f"override_{sym}_{rank}", value="",
                )
            _override_ok = (not _hostile_block) or (_override_text.strip() == _OVERRIDE_PHRASE)

            # Confirmation checkbox — required 2-step (loss-aversion friction).
            _confirm = st.checkbox(
                f"I have reviewed entry ₹{bought_price:,.0f}, SL ₹{sl_set:,.0f}, "
                f"target ₹{s.target_1:,.0f}, qty {bought_qty}, and understand "
                f"max loss = ₹{(bought_price - sl_set) * bought_qty:,.0f}",
                key=f"confirm_{sym}_{rank}",
            )
            if not _risk.can_trade:
                st.caption(f"⛔ Save disabled by risk governor: {' · '.join(_risk.reasons)}")
            elif not _sector_ok:
                st.caption(f"⛔ {_sector_reason}")
            else:
                st.caption(f"🛡️ {_sector_reason}")
            if st.button(f"💾 Save {sym}", key=f"save_{sym}_{rank}", use_container_width=True,
                         disabled=(not _risk.can_trade) or (not _sector_ok)
                                  or len(thesis.strip()) < 20
                                  or (not _override_ok)
                                  or (not _confirm)):
                state.add_position({
                    "symbol": sym, "buy_price": bought_price, "qty": bought_qty,
                    "stop_loss": sl_set, "target": s.target_1,
                    # B4: persist T1/T2 explicitly so Decay Watch can detect
                    # T1-crossings and surface concrete sell triggers.
                    "target_1": float(s.target_1),
                    "target_2": float(s.target_2),
                    "entry_date": datetime.now().strftime("%Y-%m-%d"),
                    "thesis": thesis.strip(),
                    "score_at_entry": float(sc),
                    "tape_at_entry": _tape.regime if _tape else "UNKNOWN",
                    "invested": bought_price * bought_qty,
                    "win_prob_at_entry": float(s.win_probability),
                    "rr_at_entry": float(s.risk_reward),
                    # R6 — track that this trade was opened DESPITE the engine
                    # explicitly downgrading via the HOSTILE override path.
                    "opened_against_engine": bool(_hostile_block),
                    "override_phrase_used": _override_text.strip() if _hostile_block else "",
                })
                st.success(f"✅ {sym} saved to positions. Will appear on Decay Watch next refresh.")
        st.markdown("---")

positions = state.get_positions()
if positions:
    st.markdown("## 💼 My Open Positions")
    total_invested = sum(p.get("invested", p.get("buy_price", 0) * p.get("qty", 0)) for p in positions)
    # Keep schema robust: older positions saved before the "invested" field was added still tally correctly.
    st.caption(f"Total deployed: ₹{total_invested:,.0f}")
    for i, p in enumerate(positions):
        c1, c2, c3, c4 = st.columns([3, 2, 2, 1])
        c1.markdown(f"**{p['symbol']}** — {p['qty']} shares @ ₹{p['buy_price']:.2f}")
        c1.caption(f"SL ₹{p['stop_loss']:.2f} | Target ₹{p['target']:.2f} | {p.get('entry_date') or p.get('date', '')}")
        c2.metric("Invested", f"₹{p['invested']:,.0f}")
        c3.metric("Max Loss", f"₹{(p['buy_price'] - p['stop_loss']) * p['qty']:,.0f}")
        with c4:
            if st.button("🗑️", key=f"del_{p['symbol']}_{i}"):
                state.remove_position(i); st.rerun()

    # --- Cloud-persistence helpers (Streamlit Cloud's filesystem is ephemeral) ---
    import json as _json
    st.markdown("---")
    st.markdown("### 💾 Backup / Restore")
    st.caption(
        "Streamlit Cloud's container restarts wipe local writes. Use these "
        "controls to download your positions + journal locally and re-upload "
        "after any restart."
    )
    bk1, bk2 = st.columns(2)
    backup_blob = _json.dumps({
        "positions": state.get_positions(),
        "journal": state.get_journal(),
        "exported_at": datetime.now().isoformat(timespec="seconds"),
    }, indent=2)
    bk1.download_button(
        "⬇️ Download positions + journal (JSON)",
        data=backup_blob,
        file_name=f"nse_lab_backup_{datetime.now().strftime('%Y%m%d_%H%M')}.json",
        mime="application/json",
        use_container_width=True,
    )
    uploaded = bk2.file_uploader(
        "⬆️ Upload backup to restore", type=["json"], key="pos_restore",
    )
    if uploaded is not None:
        try:
            payload = _json.loads(uploaded.read().decode("utf-8"))
            new_pos = payload.get("positions", [])
            new_journal = payload.get("journal", [])
            if isinstance(new_pos, list) and isinstance(new_journal, list):
                state.set_positions(new_pos)
                state.set_journal(new_journal)
                st.success(f"✅ Restored {len(new_pos)} positions, {len(new_journal)} journal entries.")
            else:
                st.error("Invalid backup format — expected JSON with positions[] and journal[].")
        except Exception as e:
            st.error(f"Restore failed: {e}")
