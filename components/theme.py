# ── Color tokens ──────────────────────────────────────────────────
BG       = "#050A14"       # deep space black
SURFACE  = "#0D1526"       # card background
SURFACE2 = "#1A2540"       # elevated surface
BORDER   = "#1E3A5F"       # border
TEXT     = "#E8EDF5"       # primary text
MUTED    = "#5A7390"       # muted text
GREEN    = "#00FF87"       # NSE bull green
RED      = "#FF3355"       # bear red
BLUE     = "#4D9FFF"       # info blue
AMBER    = "#FFB800"       # warning amber
CYAN     = "#00D4FF"       # highlight
PURPLE   = "#A78BFA"       # accent
GOLD     = "#FFD700"       # premium gold


def inject_css() -> str:
    return """
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Inter:wght@400;500;600;700&display=swap');

/* ── Root & Body ─────────────────────────────── */
html, body, [data-testid="stAppViewContainer"] {
    background: #050A14 !important;
    color: #E8EDF5 !important;
    font-family: 'Inter', sans-serif !important;
}
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #0A1628 0%, #050A14 100%) !important;
    border-right: 1px solid #1E3A5F !important;
}
[data-testid="stHeader"] {
    background: rgba(5,10,20,0.95) !important;
    border-bottom: 1px solid #1E3A5F !important;
    backdrop-filter: blur(10px) !important;
}

/* ── Typography ──────────────────────────────── */
h1, h2, h3 {
    font-family: 'Inter', sans-serif !important;
    font-weight: 700 !important;
    background: linear-gradient(135deg, #00FF87, #4D9FFF) !important;
    -webkit-background-clip: text !important;
    -webkit-text-fill-color: transparent !important;
    background-clip: text !important;
}
h4, h5, h6 { color: #E8EDF5 !important; }

/* ── Metrics ──────────────────────────────────── */
[data-testid="stMetric"] {
    background: linear-gradient(135deg, #0D1526, #1A2540) !important;
    border: 1px solid #1E3A5F !important;
    border-radius: 12px !important;
    padding: 16px !important;
    transition: border-color 0.2s !important;
}
[data-testid="stMetric"]:hover { border-color: #4D9FFF !important; }
[data-testid="stMetricLabel"] { color: #5A7390 !important; font-size: 11px !important; text-transform: uppercase !important; letter-spacing: 0.08em !important; }
[data-testid="stMetricValue"] { color: #E8EDF5 !important; font-family: 'JetBrains Mono', monospace !important; font-weight: 700 !important; font-size: 22px !important; }
[data-testid="stMetricDelta"] svg { display: none !important; }

/* ── Buttons ──────────────────────────────────── */
[data-testid="stBaseButton-primary"] button, [kind="primary"] {
    background: linear-gradient(135deg, #00C87A, #00FF87) !important;
    color: #050A14 !important;
    font-weight: 700 !important;
    border: none !important;
    border-radius: 8px !important;
    font-family: 'Inter', sans-serif !important;
    transition: all 0.2s !important;
    box-shadow: 0 0 20px rgba(0,255,135,0.3) !important;
}
[data-testid="stBaseButton-primary"] button:hover {
    box-shadow: 0 0 30px rgba(0,255,135,0.5) !important;
    transform: translateY(-1px) !important;
}
button[kind="secondary"] {
    background: #1A2540 !important;
    color: #E8EDF5 !important;
    border: 1px solid #1E3A5F !important;
    border-radius: 8px !important;
}

/* ── Inputs ───────────────────────────────────── */
[data-testid="stTextInput"] input, [data-testid="stSelectbox"] {
    background: #0D1526 !important;
    border: 1px solid #1E3A5F !important;
    color: #E8EDF5 !important;
    border-radius: 8px !important;
    font-family: 'JetBrains Mono', monospace !important;
}
[data-testid="stTextInput"] input:focus { border-color: #4D9FFF !important; box-shadow: 0 0 0 2px rgba(77,159,255,0.2) !important; }

/* ── Cards ────────────────────────────────────── */
.verdict-go {
    background: linear-gradient(135deg, rgba(0,255,135,0.1), rgba(0,200,122,0.05));
    border: 1px solid #00FF87;
    border-radius: 16px;
    padding: 20px 24px;
    box-shadow: 0 0 30px rgba(0,255,135,0.15);
    font-family: 'Inter', sans-serif;
    color: #E8EDF5;
}
.verdict-wait {
    background: linear-gradient(135deg, rgba(255,184,0,0.1), rgba(255,184,0,0.05));
    border: 1px solid #FFB800;
    border-radius: 16px;
    padding: 20px 24px;
    box-shadow: 0 0 30px rgba(255,184,0,0.15);
    font-family: 'Inter', sans-serif;
    color: #E8EDF5;
}
.verdict-avoid {
    background: linear-gradient(135deg, rgba(255,51,85,0.1), rgba(255,51,85,0.05));
    border: 1px solid #FF3355;
    border-radius: 16px;
    padding: 20px 24px;
    box-shadow: 0 0 30px rgba(255,51,85,0.15);
    font-family: 'Inter', sans-serif;
    color: #E8EDF5;
}
.metric-card {
    background: linear-gradient(135deg, #0D1526, #1A2540);
    border: 1px solid #1E3A5F;
    border-radius: 12px;
    padding: 14px 18px;
    transition: all 0.2s;
}
.metric-card:hover { border-color: #4D9FFF; box-shadow: 0 4px 20px rgba(77,159,255,0.1); }
.steps {
    background: linear-gradient(135deg, #0A1628, #0D1E35);
    border: 1px solid #1E3A5F;
    border-left: 4px solid #FFB800;
    border-radius: 12px;
    padding: 20px 24px;
    margin: 12px 0;
    font-family: 'Inter', sans-serif;
    color: #E8EDF5;
}
.steps li { margin: 8px 0; color: #B0BEC5; }
.steps b { color: #FFB800; }

/* ── Alerts & Notifications ───────────────────── */
[data-testid="stAlert"] {
    border-radius: 12px !important;
    border: none !important;
    font-family: 'Inter', sans-serif !important;
}
[data-testid="stAlert"][data-baseweb="notification"] {
    background: linear-gradient(135deg, rgba(77,159,255,0.1), rgba(77,159,255,0.05)) !important;
    border-left: 3px solid #4D9FFF !important;
}

/* ── Tabs ─────────────────────────────────────── */
[data-testid="stTabs"] button {
    color: #5A7390 !important;
    font-family: 'Inter', sans-serif !important;
    font-weight: 500 !important;
    border-radius: 8px 8px 0 0 !important;
    transition: all 0.2s !important;
}
[data-testid="stTabs"] button[aria-selected="true"] {
    color: #00FF87 !important;
    border-bottom: 2px solid #00FF87 !important;
    background: rgba(0,255,135,0.05) !important;
}
[data-testid="stTabs"] button:hover { color: #E8EDF5 !important; }

/* ── Progress/Slider ──────────────────────────── */
[data-testid="stSlider"] [data-baseweb="slider"] div[role="slider"] {
    background: #00FF87 !important;
    border: 2px solid #00C87A !important;
}
[data-testid="stProgress"] > div > div { background: linear-gradient(90deg, #00C87A, #00FF87) !important; }

/* ── Scrollbar ────────────────────────────────── */
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: #050A14; }
::-webkit-scrollbar-thumb { background: #1E3A5F; border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: #4D9FFF; }

/* ── Sidebar nav items ────────────────────────── */
[data-testid="stSidebarNav"] a {
    color: #5A7390 !important;
    border-radius: 8px !important;
    transition: all 0.2s !important;
    font-family: 'Inter', sans-serif !important;
}
[data-testid="stSidebarNav"] a:hover { color: #E8EDF5 !important; background: rgba(77,159,255,0.1) !important; }
[data-testid="stSidebarNav"] a[aria-current="page"] { color: #00FF87 !important; background: rgba(0,255,135,0.08) !important; border-left: 3px solid #00FF87 !important; }

/* ── Expander ─────────────────────────────────── */
[data-testid="stExpander"] {
    background: #0D1526 !important;
    border: 1px solid #1E3A5F !important;
    border-radius: 12px !important;
}
[data-testid="stExpander"] summary { color: #5A7390 !important; }

/* ── DataFrames / Tables ──────────────────────── */
[data-testid="stDataFrame"] { border-radius: 12px !important; overflow: hidden !important; }

/* ── Separator ────────────────────────────────── */
hr { border-color: #1E3A5F !important; opacity: 0.5 !important; }

/* ── Score bars (custom HTML) ─────────────────── */
.score-bar-track { background: #1A2540 !important; }

/* ───────────────────────────────────────────────────────────────────────
   MOBILE / RESPONSIVE — audit gap (mobile 2/10 → 6/10)
   The biggest mobile pain on Streamlit is st.columns(N) on narrow screens.
   These rules force columns to stack vertically below 768px, enlarge tap
   targets to 44px (Apple HIG minimum), scale fonts, and tighten padding.
─────────────────────────────────────────────────────────────────────── */
@media (max-width: 768px) {
    /* Force any st.columns container to stack vertically on phones. */
    [data-testid="stHorizontalBlock"] {
        flex-direction: column !important;
        gap: 8px !important;
    }
    [data-testid="stHorizontalBlock"] > div {
        width: 100% !important;
        flex: 1 1 100% !important;
        min-width: 100% !important;
    }

    /* Smaller metrics + tighter padding on phones so the user sees more
       at a glance without horizontal-scroll hell. */
    [data-testid="stMetric"] { padding: 10px 12px !important; }
    [data-testid="stMetricValue"] { font-size: 18px !important; }
    [data-testid="stMetricLabel"] { font-size: 10px !important; }

    /* Tap targets: Apple HIG recommends 44x44px minimum, Material 48x48.
       Streamlit defaults are ~32px which fails both standards. */
    [data-testid="stBaseButton-primary"] button,
    [data-testid="stBaseButton-secondary"] button,
    [kind="primary"], [kind="secondary"] {
        min-height: 44px !important;
        font-size: 15px !important;
        padding: 8px 16px !important;
    }

    /* Reduce typography sizes — desktop H1/H2 are absurd on phones. */
    h1 { font-size: 24px !important; line-height: 1.2 !important; }
    h2 { font-size: 20px !important; }
    h3 { font-size: 17px !important; }
    .stMarkdown p { font-size: 14px !important; }

    /* Tighten card padding so content doesn't waste vertical real estate. */
    .verdict-go, .verdict-wait, .verdict-avoid {
        padding: 14px 16px !important;
        border-radius: 12px !important;
    }
    .steps {
        padding: 14px 16px !important;
        font-size: 13px !important;
    }
    .steps li { margin: 6px 0 !important; }

    /* Sidebar: hide by default on mobile so the home view isn't blocked.
       User can still tap the hamburger to open it. */
    [data-testid="stSidebar"][aria-expanded="true"] {
        width: 80vw !important;
        max-width: 320px !important;
    }
}

/* Extra-small phones (≤ 380px wide — older iPhone SE) */
@media (max-width: 380px) {
    [data-testid="stMetricValue"] { font-size: 15px !important; }
    h1 { font-size: 20px !important; }
    .stMarkdown p { font-size: 13px !important; }
}
</style>
"""
