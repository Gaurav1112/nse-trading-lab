"""
6_Backtest.py — Strategy Backtester
=====================================
Migrated from ui.py lines 1067-1127.
Runs all STRATEGIES against a symbol, ranks by Sharpe ratio,
and optionally validates the best strategy via walk-forward.
"""

import re
import streamlit as st
import pandas as pd
from datetime import date
from components import theme, state, charts
from nse_backtest.data import fetch_nse
from nse_backtest.strategies import STRATEGIES
from nse_backtest.engine import run_backtest, TradeConfig
from nse_backtest.analytics import compute_metrics
from nse_backtest.risk import walk_forward_validate
from nse_backtest.sample_data import trending_stock

SYMBOL_RE = re.compile(r"^[A-Z0-9&.\-^]{1,20}$")

st.set_page_config(page_title="Backtest | Trading Lab", page_icon="🧪", layout="wide")
st.markdown(theme.inject_css(), unsafe_allow_html=True)
state.init_session()

st.markdown("# 🧪 Strategy Backtester")

bc1, bc2, bc3, bc4 = st.columns([2, 2, 1, 1])
sym = bc1.text_input("Symbol", value="RELIANCE", key="bt_sym").upper().strip()
from_date = bc2.date_input("From", value=date(2020, 1, 1), key="bt_from")
use_demo = bc3.checkbox("Demo", key="bt_demo")
walk_fwd = bc4.checkbox("Walk-Forward", key="bt_wf",
                         help="Out-of-sample validation across 5 time splits")

if st.button("▶  Run Backtest", type="primary", use_container_width=True, key="bt_run"):
    with st.spinner(f"Backtesting {sym}…"):
        try:
            if use_demo:
                df = trending_stock()
            else:
                if not SYMBOL_RE.match(sym):
                    st.error(f"Invalid symbol: {sym!r}")
                    st.stop()
                df = fetch_nse(sym, start=str(from_date))
                if df is None or len(df) < 50:
                    st.warning("Insufficient data — using demo.")
                    df = trending_stock()

            cap = state.get_capital()
            results_list = []

            for name, fn in STRATEGIES.items():
                try:
                    df_sig = fn(df)
                    cfg = TradeConfig(
                        initial_capital=cap,
                        stop_loss_pct=state.get_sl_pct() / 100,
                        take_profit_pct=0.15,
                        position_pct=0.5,
                    )
                    result = run_backtest(df_sig, cfg)
                    m = compute_metrics(result)
                    m["strategy_name"] = name
                    results_list.append((m, result))
                except Exception:
                    pass

            if not results_list:
                st.error("No results.")
                st.stop()

            results_list.sort(
                key=lambda x: x[0].get("sharpe_ratio", -99), reverse=True
            )
            st.session_state["bt_results"] = results_list
            st.session_state["bt_sym_display"] = sym

            if walk_fwd:
                best_name = results_list[0][0]["strategy_name"]
                with st.spinner("Running walk-forward validation…"):
                    wf = walk_forward_validate(
                        df,
                        STRATEGIES[best_name],
                        TradeConfig(initial_capital=cap),
                    )
                st.session_state["bt_wf_result"] = (best_name, wf)
            else:
                # Clear stale walk-forward data when toggle is off
                st.session_state.pop("bt_wf_result", None)

        except Exception as e:
            st.error(f"Backtest failed: {e}")

# ── Results display ──────────────────────────────────────────────────────────

results_list = st.session_state.get("bt_results", [])
if results_list:
    bt_sym = st.session_state.get("bt_sym_display", sym)
    st.markdown(f"### {bt_sym} — {len(results_list)} strategies")

    rows = [
        {
            "Strategy": m["strategy_name"],
            "Return %": round(m.get("total_return_pct", 0), 1),
            "Sharpe": round(m.get("sharpe_ratio", 0), 2),
            "Max DD %": round(m.get("max_drawdown_pct", 0), 1),
            "Win %": round(m.get("win_rate_pct", 0), 1),
            "Trades": m.get("total_trades", 0),
            "Final ₹": round(m.get("final_equity", 0), 0),
        }
        for m, _ in results_list
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True)

    st.plotly_chart(
        charts.make_equity_curve(
            [r for _, r in results_list],
            [m["strategy_name"] for m, _ in results_list],
        ),
        use_container_width=True,
    )

    best_m, best_r = results_list[0]
    rm1, rm2, rm3, rm4 = st.columns(4)
    rm1.metric("Best", best_m["strategy_name"])
    rm2.metric("Sharpe", f"{best_m.get('sharpe_ratio', 0):.2f}")
    rm3.metric("Max DD", f"{best_m.get('max_drawdown_pct', 0):.1f}%")
    rm4.metric("Win Rate", f"{best_m.get('win_rate_pct', 0):.1f}%")

    # ── Walk-Forward Results ─────────────────────────────────────────────────

    wf_data = st.session_state.get("bt_wf_result")
    if wf_data:
        wf_name, wf_results = wf_data
        st.markdown(f"### Walk-Forward Validation: {wf_name}")
        if wf_results:
            avg_ret = sum(r.get("return_pct", 0) for r in wf_results) / len(wf_results)
            avg_sharpe = sum(r.get("sharpe", 0) for r in wf_results) / len(wf_results)
            profitable_splits = sum(
                1 for r in wf_results if r.get("sharpe", 0) > 0
            )
            wc1, wc2, wc3 = st.columns(3)
            wc1.metric("Avg OOS Return", f"{avg_ret:.1f}%")
            wc2.metric("Avg OOS Sharpe", f"{avg_sharpe:.2f}")
            wc3.metric("Profitable Splits", f"{profitable_splits}/{len(wf_results)}")

            if avg_sharpe > 0:
                st.success("✅ Strategy shows out-of-sample edge")
            else:
                st.warning("⚠️ No OOS edge — possible overfitting")

            wf_df = pd.DataFrame(
                [
                    {
                        "Split": r["split"],
                        "Period": f"{r['test_start']} → {r['test_end']}",
                        "Return %": round(r["return_pct"], 1),
                        "Sharpe": round(r["sharpe"], 2),
                        "Win %": round(r["win_rate"], 1),
                        "Trades": r["trades"],
                    }
                    for r in wf_results
                ]
            )
            st.dataframe(wf_df, use_container_width=True)
        else:
            st.info("Walk-forward returned no splits (need more data).")

    # ── Trade Log ───────────────────────────────────────────────────────────

    with st.expander("Trade Log (best strategy)"):
        trades = best_r.get("trades", [])
        if trades:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Entry": str(t.entry_date)[:10],
                            "Exit": str(t.exit_date)[:10],
                            "In ₹": round(t.entry_price, 0),
                            "Out ₹": round(t.exit_price, 0),
                            "P&L ₹": round(t.pnl, 0),
                            "P&L %": round(t.pnl_pct * 100, 1),
                            "Reason": t.exit_reason,
                        }
                        for t in trades
                    ]
                ),
                use_container_width=True,
            )
        else:
            st.info("No completed trades for the best strategy.")
