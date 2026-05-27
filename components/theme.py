BG = "#0a0e17"
SURFACE = "#111827"
SURFACE2 = "#1a2035"
BORDER = "#1e2a42"
TEXT = "#e2e8f0"
MUTED = "#64748b"
GREEN = "#10b981"
RED = "#ef4444"
BLUE = "#3b82f6"
AMBER = "#f59e0b"
CYAN = "#06b6d4"
PURPLE = "#8b5cf6"
GREEN_BG = "#052e16"
RED_BG = "#2a0a0a"
BLUE_BG = "#0c1929"


def inject_css() -> str:
    """Return full CSS string. Call via st.markdown(inject_css(), unsafe_allow_html=True)."""
    return """<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=DM+Sans:wght@400;500;600;700&display=swap');
:root {
    --bg:#0a0e17;--surface:#111827;--surface2:#1a2035;--border:#1e2a42;
    --text:#e2e8f0;--muted:#64748b;--green:#10b981;--red:#ef4444;
    --blue:#3b82f6;--amber:#f59e0b;--cyan:#06b6d4;--purple:#8b5cf6;
    --green-bg:#052e16;--red-bg:#2a0a0a;--blue-bg:#0c1929;
}
.block-container{padding:0.5rem 1rem;max-width:100%}
.stApp{font-family:'DM Sans',sans-serif}
h1,h2,h3{font-family:'DM Sans',sans-serif!important;font-weight:700!important;letter-spacing:-0.02em}
code,.stCode{font-family:'JetBrains Mono',monospace!important}
div[data-testid="stMetric"]{background:var(--surface);padding:12px 16px;border-radius:10px;border:1px solid var(--border);transition:border-color 0.2s}
div[data-testid="stMetric"]:hover{border-color:var(--blue)}
div[data-testid="stMetric"] label{font-size:11px!important;color:var(--muted)!important;text-transform:uppercase;letter-spacing:0.05em}
div[data-testid="stMetric"] div[data-testid="stMetricValue"]{font-size:22px!important;font-family:'JetBrains Mono',monospace!important}
.verdict-go{background:linear-gradient(135deg,#052e16,#064e3b);color:#6ee7b7;padding:20px 24px;border-radius:14px;text-align:center;font-size:28px;font-weight:700;border:1px solid #10b981;box-shadow:0 0 30px rgba(16,185,129,.15)}
.verdict-wait{background:linear-gradient(135deg,#451a03,#78350f);color:#fcd34d;padding:20px 24px;border-radius:14px;text-align:center;font-size:28px;font-weight:700;border:1px solid #f59e0b;box-shadow:0 0 30px rgba(245,158,11,.15)}
.verdict-avoid{background:linear-gradient(135deg,#450a0a,#7f1d1d);color:#fca5a5;padding:20px 24px;border-radius:14px;text-align:center;font-size:28px;font-weight:700;border:1px solid #ef4444;box-shadow:0 0 30px rgba(239,68,68,.15)}
.card{background:var(--surface);padding:16px;border-radius:10px;border-left:3px solid var(--blue);margin:8px 0}
.card-g{background:var(--green-bg);padding:16px;border-radius:10px;border-left:3px solid var(--green);margin:8px 0}
.card-r{background:var(--red-bg);padding:16px;border-radius:10px;border-left:3px solid var(--red);margin:8px 0}
.card-y{background:#1a1a06;padding:12px 16px;border-radius:8px;border-left:3px solid var(--amber);margin:6px 0;font-size:14px}
.gauge{display:inline-flex;align-items:center;justify-content:center;width:90px;height:90px;border-radius:50%;font-family:'JetBrains Mono',monospace;font-size:24px;font-weight:700}
.gauge-go{background:conic-gradient(#10b981 var(--pct),#1e293b var(--pct));color:#6ee7b7}
.gauge-wait{background:conic-gradient(#f59e0b var(--pct),#1e293b var(--pct));color:#fcd34d}
.gauge-avoid{background:conic-gradient(#ef4444 var(--pct),#1e293b var(--pct));color:#fca5a5}
.steps{background:var(--surface2);border:1px solid var(--border);border-radius:12px;padding:18px;margin:10px 0}
.steps ol li{margin:8px 0;font-size:14px;line-height:1.6}
.stTabs [data-baseweb="tab-list"]{gap:4px}
.stTabs [data-baseweb="tab"]{padding:10px 20px;border-radius:8px 8px 0 0}
section[data-testid="stSidebar"]{background:var(--surface);border-right:1px solid var(--border)}
@media(max-width:768px){
    .block-container{padding:.3rem .5rem!important}
    h1{font-size:22px!important}
    div[data-testid="stMetric"] div[data-testid="stMetricValue"]{font-size:16px!important}
    .verdict-go,.verdict-wait,.verdict-avoid{font-size:20px;padding:14px 16px}
    button{min-height:44px!important}
    .stTextInput input{font-size:16px!important}
}
</style>"""
