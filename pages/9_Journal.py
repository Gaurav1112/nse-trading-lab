import os
import streamlit as st
import pandas as pd
from datetime import date
from components import theme, state
from components.security import SYMBOL_RE, _safe_csv

st.set_page_config(page_title="Journal | Trading Lab", page_icon="📋", layout="wide")
st.markdown(theme.inject_css(), unsafe_allow_html=True)
state.init_session()
st.markdown("# 📋 Trade Journal")
st.markdown("_Record every trade. Journal saves automatically._")


with st.expander("➕ Add New Trade", expanded=not bool(state.get_journal())):
    jc1, jc2 = st.columns(2)
    with jc1:
        j_sym = st.text_input("Stock Symbol", "", key="j_sym", placeholder="COALINDIA")
        j_dir = st.selectbox("Direction", ["LONG", "SHORT"], key="j_dir")
        j_date = st.date_input("Trade Date", key="j_d")
    with jc2:
        j_entry = st.number_input("Entry Price ₹", value=0.0, min_value=0.0, format="%.2f", key="j_e")
        j_exit = st.number_input("Exit Price ₹ (0 if open)", value=0.0, min_value=0.0, format="%.2f", key="j_x")
        j_qty = st.number_input("Quantity", value=0, min_value=0, key="j_q")
    j_mode = st.selectbox("Trading Mode", ["Swing", "Positional", "Long-term", "Intraday", "Options"], key="j_mode")
    j_reason = st.text_area("Why I took this trade", "", key="j_r", height=60)
    j_lesson = st.text_area("Lesson learned (fill after exit)", "", key="j_l", height=60)

    if st.button("💾  Save Trade", type="primary", use_container_width=True):
        # Inline symbol validation (same regex the analyse page uses)
        try:
            s = (j_sym or "").upper().strip()
            if s and not SYMBOL_RE.match(s):
                raise ValueError(f"Invalid symbol: {j_sym!r}")
            j_sym_clean = s
        except ValueError as ve:
            j_sym_clean = None
            st.error(f"Invalid symbol: {ve}")
        if j_sym_clean is None:
            pass
        elif not j_sym_clean:
            st.error("Enter a stock symbol")
        elif j_entry <= 0:
            st.error("Enter a valid entry price")
        elif j_qty <= 0:
            st.error("Enter quantity")
        else:
            pnl = (j_exit - j_entry) * j_qty * (1 if j_dir == "LONG" else -1) if j_exit > 0 else 0
            trade = {
                "date": str(j_date), "symbol": j_sym_clean, "dir": j_dir,
                "mode": j_mode, "entry": j_entry, "exit": j_exit, "qty": j_qty,
                "pnl": round(pnl, 2), "reason": j_reason, "lesson": j_lesson,
                "status": "CLOSED" if j_exit > 0 else "OPEN"
            }
            state.add_trade(trade)
            st.success(f"✅ Saved {j_sym_clean}")
            st.rerun()

# Display journal
journal = state.get_journal()
if journal:
    st.markdown("---")
    # Summary metrics
    jdf = pd.DataFrame(journal)
    total_pnl = jdf["pnl"].sum()
    closed = jdf[jdf.get("status", "CLOSED") == "CLOSED"] if "status" in jdf.columns else jdf[jdf["exit"] > 0]
    open_trades = jdf[jdf.get("status", "CLOSED") != "CLOSED"] if "status" in jdf.columns else jdf[jdf["exit"] == 0]
    total_trades = len(jdf)
    wins = (jdf["pnl"] > 0).sum()
    losses = (jdf["pnl"] < 0).sum()

    mc = st.columns(5)
    pnl_color = "normal" if total_pnl >= 0 else "inverse"
    mc[0].metric("Total P&L", f"₹{total_pnl:,.0f}", delta=f"{'Profit' if total_pnl > 0 else 'Loss'}", delta_color=pnl_color)
    mc[1].metric("Win Rate", f"{wins/total_trades*100:.0f}%" if total_trades > 0 else "—")
    mc[2].metric("W / L", f"{wins} / {losses}")
    mc[3].metric("Open", f"{len(open_trades)}")
    mc[4].metric("Total", f"{total_trades}")

    # Table
    st.markdown("### All Trades")
    display_cols = ["date", "symbol", "dir", "entry", "exit", "qty", "pnl"]
    if "mode" in jdf.columns:
        display_cols.insert(3, "mode")
    if "status" in jdf.columns:
        display_cols.append("status")
    tdf = jdf[display_cols].copy()
    tdf["pnl"] = tdf["pnl"].apply(lambda x: f"₹{x:,.0f}")
    st.dataframe(tdf, use_container_width=True, hide_index=True)

    # By mode breakdown
    if "mode" in jdf.columns and jdf["mode"].nunique() > 1:
        st.markdown("### By Trading Mode")
        mode_stats = jdf.groupby("mode").agg(
            Trades=("pnl", "count"), PnL=("pnl", "sum"),
            Wins=("pnl", lambda x: (x > 0).sum())
        ).reset_index()
        mode_stats["Win%"] = (mode_stats["Wins"] / mode_stats["Trades"] * 100).round(0)
        mode_stats["PnL"] = mode_stats["PnL"].apply(lambda x: f"₹{x:,.0f}")
        st.dataframe(mode_stats, use_container_width=True, hide_index=True)

    # Export and clear
    ec1, ec2 = st.columns(2)
    with ec1:
        st.download_button("📥 Export CSV", _safe_csv(jdf), "trade_journal.csv", "text/csv")
    with ec2:
        if st.button("🗑️ Clear All Trades"):
            st.session_state["journal"] = []
            state._save_journal_to_disk([])
            st.rerun()
else:
    st.info("No trades recorded yet. Add your first trade above.")
