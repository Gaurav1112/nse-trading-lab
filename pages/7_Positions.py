import re
import streamlit as st
import pandas as pd
from components import theme, state

st.set_page_config(page_title="Positions | Trading Lab", page_icon="💼", layout="wide")
st.markdown(theme.inject_css(), unsafe_allow_html=True)
state.init_session()
st.markdown("# 💼 MTF Position Tracker")


def _safe_csv(df) -> str:
    def _csv_safe(v):
        s = str(v)
        if s and s[0] in ('=', '+', '-', '@', '|', '%'):
            return "'" + s
        return s
    if df is None or df.empty:
        return df.to_csv(index=False) if df is not None else ""
    safe = df.map(_csv_safe) if hasattr(df, "map") else df.applymap(_csv_safe)
    return safe.to_csv(index=False)


def _safe_filename(name: str, fallback: str = "export") -> str:
    s = re.sub(r"[^A-Za-z0-9_\-]", "_", str(name or ""))[:40]
    return s or fallback


try:
    p1, p2, p3 = st.columns(3)
    with p1: ps = st.text_input("Symbol", "ARSSBL"); psh = st.number_input("Shares", value=2202, min_value=1)
    with p2: pa = st.number_input("Avg ₹", value=513.0, min_value=0.1, format="%.2f"); pc = st.number_input("Current ₹", value=470.0, min_value=0.1, format="%.2f")
    with p3: pm = st.number_input("MTF %/yr", value=18.0, min_value=0.0); pd_ = st.number_input("Days", value=60, min_value=0)

    if st.button("📊  Analyze", type="primary", use_container_width=True):
        inv = pa * psh; cv = pc * psh; pnl = cv - inv; pnl_p = (pc / pa - 1) * 100 if pa > 0 else 0
        # MTF: only the borrowed portion (75% at 25% margin) accrues interest
        borrowed_frac = 0.75
        di = inv * borrowed_frac * (pm / 100) / 365 if pm > 0 else 0
        ti = inv * borrowed_frac * ((1 + pm / 100 / 365) ** pd_ - 1) if pm > 0 else 0  # compound
        be = pa + (ti / psh if psh > 0 else 0); rec = ((be - pc) / pc * 100) if pc > 0 else 0
        m = st.columns(4)
        m[0].metric("Invested (own)", f"₹{inv * 0.25:,.0f}" if pm > 0 else f"₹{inv:,.0f}")
        m[1].metric("Current Value", f"₹{cv:,.0f}")
        m[2].metric("P&L", f"₹{pnl:,.0f}", delta=f"{pnl_p:+.1f}%", delta_color="normal" if pnl >= 0 else "inverse")
        m[3].metric("Breakeven", f"₹{be:,.2f}")
        if di > 0:
            st.markdown("### MTF Interest Burn _(on 75% borrowed)_")
            i1, i2, i3, i4 = st.columns(4)
            i1.metric("Daily", f"₹{di:,.0f}"); i2.metric("Monthly", f"₹{di * 30:,.0f}")
            i3.metric("Paid so far", f"₹{ti:,.0f}"); i4.metric("Recovery needed", f"{rec:.1f}%")
            proj = [{"Days ahead": d,
                     "Total interest": f"₹{inv * borrowed_frac * ((1 + pm/100/365)**(pd_+d) - 1):,.0f}",
                     "New breakeven": f"₹{pa + inv * borrowed_frac * ((1 + pm/100/365)**(pd_+d) - 1) / psh:,.2f}"}
                    for d in [7, 14, 30, 60, 90]]
            st.dataframe(pd.DataFrame(proj), use_container_width=True, hide_index=True)
        st.markdown("### Exit Scenarios _(after all charges)_")
        lo = max(int(min(pc, pa) * 0.80), 1); hi = max(int(max(pa, pc) * 1.15), lo + 10)
        step = max(int((hi - lo) / 15), 1)
        exits = []
        for ep in range(lo, hi + step, step):
            gross = (ep - pa) * psh
            # Realistic exit costs: STT on sell (0.1%) + DP charge (₹15.93)
            exit_fees = ep * psh * 0.001 + 15.93
            net = gross - ti - exit_fees
            exits.append({"Price": f"₹{ep}", "Gross P&L": f"₹{gross:,.0f}",
                           "Interest+Fees": f"₹{ti + exit_fees:,.0f}",
                           "Net P&L": f"₹{net:,.0f}", "": "✅" if net > 0 else "❌"})
        st.dataframe(pd.DataFrame(exits), use_container_width=True, hide_index=True)
        st.caption("_Exit fees = STT 0.1% + DP ₹15.93 per sell_")
        st.download_button("📥 Export", _safe_csv(pd.DataFrame(exits)), f"{_safe_filename(ps)}_exit.csv", "text/csv")
except Exception as e:
    st.error(f"Error: {e}")
