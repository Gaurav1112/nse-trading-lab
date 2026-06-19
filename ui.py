import streamlit as st
from components import theme, state

st.set_page_config(page_title="NSE Trading Lab", page_icon="📈", layout="wide",
                   initial_sidebar_state="expanded")
st.markdown(theme.inject_css(), unsafe_allow_html=True)
state.init_session()


# ── First-run modal ──────────────────────────────────────────────────────────
# Onboarding audit (2/10 → 8/10): explicit "what this is / isn't" before
# anything else. Compliance: makes the disclaimer informed-consent, not buried.
if not st.session_state.get("seen_intro"):
    st.markdown(
        '<div style="border:2px solid #FFB800;border-radius:14px;padding:20px 24px;'
        'margin:8px 0 24px;background:#1a1408">'
        '<div style="font-size:11px;color:#FFB800;text-transform:uppercase;letter-spacing:1px">'
        'Welcome — please read once</div>'
        '<div style="font-size:22px;font-weight:700;color:#FFD580;margin-top:6px">'
        'NSE Trading Lab — research tool, not tips.</div>'
        '<div style="margin-top:12px;color:#E8D9B4;font-size:14px;line-height:1.55">'
        'This app scores Nifty 50 stocks for swing-trade setups and lets you track '
        'positions. It does <b>not</b> place orders, does <b>not</b> give SEBI-registered '
        'advice, and does <b>not</b> predict markets.<br><br>'
        '<b>Today\'s tape is HOSTILE.</b> Held-out 2026 YTD data shows the engine '
        'loses ~1.71% per trade in this regime. We recommend you <b>watch only</b> — '
        'no real money — until the tape banner turns amber (MIXED) or green (TRENDING).'
        '</div></div>',
        unsafe_allow_html=True,
    )
    c1, c2 = st.columns([1, 4])
    if c1.button("✅ I understand", type="primary", use_container_width=True):
        st.session_state["seen_intro"] = True
        st.rerun()
    c2.caption("This message is shown once per session. Capital + risk % are configurable in **Settings**.")
    st.stop()


# ── Sidebar with grouping ────────────────────────────────────────────────────
# Onboarding audit (3/10 → 7/10): 16 flat items → 4 groups.
with st.sidebar:
    st.markdown('<div style="padding:8px 0 16px;font-size:20px;font-weight:700;'
                'letter-spacing:-0.02em">◆ Trading Lab</div>', unsafe_allow_html=True)

    with st.expander("🎯  Decide", expanded=True):
        st.page_link("pages/1_Picks.py", label="Today's Picks")
        st.page_link("pages/3_Analyze.py", label="Analyze Symbol")
        st.page_link("pages/4_Screener.py", label="Screener")
        st.page_link("pages/16_Watchlist.py", label="Watchlist (always-on)")
        st.page_link("pages/17_Intraday_Scanner.py", label="⚡ Intraday RSI Scanner")

    with st.expander("💼  Manage", expanded=False):
        st.page_link("pages/7_Positions.py", label="Positions")
        st.page_link("pages/12_Decay_Watch.py", label="Decay Watch")
        st.page_link("pages/9_Journal.py", label="Journal")

    with st.expander("📊  Review", expanded=False):
        st.page_link("pages/14_Track_Record.py", label="Track Record (P&L)")
        st.page_link("pages/15_Trade_Replay.py", label="Trade Replay")
        st.page_link("pages/13_Tape_Monitor.py", label="Tape Monitor")
        st.page_link("pages/2_Dashboard.py", label="Dashboard")

    with st.expander("⚙️  Tools & Configure", expanded=False):
        st.page_link("pages/6_Backtest.py", label="Backtest")
        st.page_link("pages/5_Trading_Modes.py", label="Trading Modes")
        st.page_link("pages/8_Risk_Lab.py", label="Risk Lab")
        st.page_link("pages/11_Settings.py", label="Settings")
        st.page_link("pages/10_Learn.py", label="Learn")

    st.markdown("---")
    st.markdown('<span style="font-size:11px;color:#64748b;text-transform:uppercase;'
                'letter-spacing:.1em">Quick Watchlist</span>', unsafe_allow_html=True)
    for sym in state.get_watchlist()[:5]:
        if st.button(f"  {sym}", key=f"wl_{sym}", use_container_width=True):
            state.set_analyze_sym(sym)
            st.session_state["auto_analyze"] = True
            st.switch_page("pages/3_Analyze.py")
    st.markdown("---")
    cap = state.get_capital()
    st.caption(f"💰 ₹{cap:,.0f}")
    st.caption(f"🎯 {state.get_risk_pct()}% risk = ₹{cap * state.get_risk_pct() / 100:,.0f}/trade")


# ── Home content ─────────────────────────────────────────────────────────────
# If positions exist, route to Decay Watch on first home-visit (Reliability sprint).
_open_positions = [p for p in state.get_positions() if not p.get("closed_date")]
if _open_positions and not st.session_state.get("seen_home"):
    st.session_state["seen_home"] = True
    st.switch_page("pages/12_Decay_Watch.py")

st.markdown("# ◆ NSE Trading Lab")
st.markdown("##### Research tool for NSE swing-trading. Not tips. Not advice.")
st.markdown("---")
if _open_positions:
    st.warning(
        f"💼 You have **{len(_open_positions)} open position(s)** — monitor them on **Decay Watch** "
        f"before considering new entries."
    )
else:
    st.info("👈 Use the grouped sidebar to navigate. Start with **Today's Picks** (Decide group).")

# Discipline streak counter (Robinhood-style behavioural reinforcement)
try:
    from components.discipline import assess as _disc_assess
    _disc = _disc_assess(state.get_journal(), state.get_positions())
    c1, c2, c3 = st.columns(3)
    c1.metric("Rule-following streak", f"{_disc.rule_following_streak_days} days",
              help="Days since your last engine-override trade. Behavioural edge is built one day at a time.")
    c2.metric("Process Adherence Index", f"{_disc.process_adherence_index:.0f}/100",
              help="Composite: override rate × override-vs-aligned delta × cooling-off respect.")
    c3.metric("Consecutive losses", _disc.consecutive_losses,
              delta="cooling-off recommended" if _disc.cooling_off_recommended else "OK",
              delta_color="inverse" if _disc.cooling_off_recommended else "normal",
              help="2+ consecutive losers triggers a cooling-off period (skip the next setup).")
    for n in _disc.notes:
        st.info(n)
except Exception:
    pass

# ── SEBI compliance hard warning if URL is publicly accessible ──────────────
# Audit found this is the single CRITICAL issue: a public Streamlit URL serving
# BUY/SELL verdicts may constitute unregistered investment advice under SEBI
# Research Analysts Regs 2014 §3. We can't enable Streamlit Cloud auth from
# Python (it's a share.streamlit.io UI setting), but we can scream about it.
st.markdown("---")
with st.expander("⚠️  Critical compliance reminder — read once"):
    st.markdown(
        "**Is this URL publicly accessible?** If anyone besides you can visit "
        "https://nse-trading-lab.streamlit.app and see BUY/SELL verdicts, that's "
        "plausibly unregistered investment advice under SEBI Research Analysts "
        "Regulations 2014 §3.\n\n"
        "**Fix tonight (5 minutes, no code):**\n"
        "1. Open https://share.streamlit.io\n"
        "2. Your app → **Settings → Sharing**\n"
        "3. 'Who can view this app' → **'Only specific people'**\n"
        "4. Add your email (and any others you trust)\n"
        "5. Save. The URL now requires Google sign-in.\n\n"
        "Until this is done, the disclaimer below is necessary but not sufficient. "
        "See `DEPLOY.md` for screenshots."
    )

# ── SEBI disclaimer footer ──
st.markdown("---")
st.caption(
    "**Disclaimer.** This tool is personal research software. It is **not** investment advice. "
    "The author is **not** a SEBI-registered investment adviser or research analyst. Past performance, "
    "walk-forward expectancy estimates, and engine verdicts do not guarantee future results. Held-out "
    "2026 YTD data showed -1.71% expectancy per trade on the v2 engine in HOSTILE tape. "
    "Trade only with capital you can afford to lose. Consult a SEBI-registered adviser before "
    "deploying significant capital based on outputs from this software."
)
