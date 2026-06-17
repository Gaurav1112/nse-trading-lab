"""Single helper for rendering instructional empty states across pages."""
from __future__ import annotations

import streamlit as st


def render_empty(title: str, why: str, cta_label: str | None = None,
                 cta_page: str | None = None) -> None:
    """Render a clean empty-state card on any page.

    title: short headline (e.g., "No picks today")
    why: 1-2 sentence explanation
    cta_label / cta_page: optional button taking the user to a related page
    """
    st.markdown(
        f'<div style="text-align:center;padding:48px 24px;background:#0D1526;'
        f'border:1px dashed #2A3A52;border-radius:14px;margin:16px 0">'
        f'<div style="font-size:48px;margin-bottom:8px">🌙</div>'
        f'<div style="font-size:20px;font-weight:700;color:#C9D5E0">{title}</div>'
        f'<div style="margin-top:8px;color:#7A93AA;font-size:14px;line-height:1.5">{why}</div>'
        f'</div>', unsafe_allow_html=True,
    )
    if cta_label and cta_page:
        if st.button(cta_label, use_container_width=True):
            st.switch_page(cta_page)
