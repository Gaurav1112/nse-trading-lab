import streamlit as st
import os
from components import theme, state
from components.security import SYMBOL_RE
from nse_backtest.strategies import STRATEGIES
from nse_backtest.data import NIFTY100_SYMBOLS

st.set_page_config(page_title="Settings | Trading Lab", page_icon="⚙️", layout="wide")
st.markdown(theme.inject_css(), unsafe_allow_html=True)
state.init_session()
st.markdown("# ⚙️ Settings")

cap = st.number_input("Capital (₹)", min_value=1_000.0, max_value=1e8,
                       value=state.get_capital(), step=10_000.0, format="%.0f")
risk = st.slider("Risk per trade (%)", 0.5, 5.0, state.get_risk_pct(), 0.5)
sl = st.slider("Default Stop Loss (%)", 1.0, 15.0, state.get_sl_pct(), 0.5)
if st.button("💾 Save Settings", type="primary"):
    state.set_capital(cap); state.set_risk_pct(risk); state.set_sl_pct(sl)
    st.success(f"✅ Saved — max loss/trade: ₹{cap * risk / 100:,.0f}")

st.markdown("---")
st.markdown("**Watchlist**")
wl_str = st.text_input("Symbols (comma-separated)", value=", ".join(state.get_watchlist()))
if st.button("Update Watchlist"):
    new_wl = [s.strip().upper() for s in wl_str.split(",") if s.strip() and SYMBOL_RE.match(s.strip().upper())]
    state.set_watchlist(new_wl)
    st.success(f"Updated: {new_wl}")
st.markdown("---")
st.caption(f"Strategies: {len(STRATEGIES)} | Nifty 100 universe: {len(NIFTY100_SYMBOLS)} stocks")

st.markdown("### 🔔 Notifications")
public_key = os.environ.get("VAPID_PUBLIC_KEY", "PUT-YOUR-PUBLIC-KEY-HERE")
st.markdown(
    f"""
    <button id="enable-push" style="background:#00FF87;color:#000;padding:10px 18px;border-radius:8px;border:0;font-weight:700">
      Enable push notifications
    </button>
    <div id="push-status" style="margin-top:8px;color:#7A93AA"></div>
    <script>
    const VAPID_PUB = '{public_key}';
    function urlBase64ToUint8Array(base64String) {{
      const padding = '='.repeat((4 - base64String.length % 4) % 4);
      const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
      const raw = atob(base64);
      const output = new Uint8Array(raw.length);
      for (let i = 0; i < raw.length; ++i) output[i] = raw.charCodeAt(i);
      return output;
    }}
    document.getElementById('enable-push').onclick = async () => {{
      const perm = await Notification.requestPermission();
      if (perm !== 'granted') {{ document.getElementById('push-status').innerText = 'Permission denied'; return; }}
      const reg = await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.subscribe({{
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(VAPID_PUB),
      }});
      // POST the subscription to a paste-bin file (user pastes into signals repo manually in v1)
      document.getElementById('push-status').innerText =
        'Copy this JSON into signals-repo/state/push_subscriptions.json:\\n\\n' + JSON.stringify(sub);
    }};
    </script>
    """,
    unsafe_allow_html=True,
)
