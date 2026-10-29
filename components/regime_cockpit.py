from __future__ import annotations
import streamlit as st

_REGIME_COLOR = {"TRENDING": "#00FF87", "MIXED": "#FFB800", "HOSTILE": "#FF4D4D"}


def build_cockpit_html(regime: str, nifty_close: float, vix: float | None,
                       breadth_pct: float | None, ema_slope: float | None) -> str:
    color = _REGIME_COLOR.get(regime, "#5A7390")
    vix_txt = f"{vix:.1f}" if vix is not None else "—"
    br_txt = f"{breadth_pct:.0f}%" if breadth_pct is not None else "—"
    slope_txt = f"{ema_slope:+.2f}%/20d" if ema_slope is not None else "—"
    return f"""
    <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px">
      <div style="border:2px solid {color};border-radius:10px;padding:12px;background:#0D1526">
        <div style="font-size:11px;color:#7A93AA">TAPE</div>
        <div style="font-size:20px;font-weight:700;color:{color}">{regime}</div>
        <div style="font-size:12px;color:#C9D5E0">Nifty {nifty_close:,.0f}</div>
      </div>
      <div style="border:1px solid #1E3A5F;border-radius:10px;padding:12px;background:#0D1526">
        <div style="font-size:11px;color:#7A93AA">INDIA VIX</div>
        <div style="font-size:20px;font-weight:700">{vix_txt}</div>
      </div>
      <div style="border:1px solid #1E3A5F;border-radius:10px;padding:12px;background:#0D1526">
        <div style="font-size:11px;color:#7A93AA">BREADTH</div>
        <div style="font-size:20px;font-weight:700">{br_txt}</div>
      </div>
      <div style="border:1px solid #1E3A5F;border-radius:10px;padding:12px;background:#0D1526">
        <div style="font-size:11px;color:#7A93AA">NIFTY vs 200EMA</div>
        <div style="font-size:20px;font-weight:700">{slope_txt}</div>
      </div>
    </div>
    """


def render_cockpit(latest: dict, vix: float | None = None, breadth_pct: float | None = None) -> None:
    regime = latest.get("regime", "UNKNOWN")
    conds = latest.get("regime_conditions", {})
    st.markdown(
        build_cockpit_html(
            regime=regime,
            nifty_close=conds.get("nifty_close", 0.0),
            vix=vix,
            breadth_pct=breadth_pct,
            ema_slope=conds.get("ema_200_slope_pct_20d"),
        ),
        unsafe_allow_html=True,
    )
