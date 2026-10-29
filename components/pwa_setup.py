import streamlit as st


def inject_pwa() -> None:
    """Inject PWA manifest link + service-worker registration script.

    Streamlit doesn't natively serve static/, so this uses raw.githubusercontent
    URLs after the code repo is pushed. Local dev: point PWA_STATIC_BASE env var
    to a local server.

    NOTE: PWA_STATIC_BASE default contains "USER" as placeholder — must be replaced
    with actual GitHub username before deployment.
    """
    import os
    base = os.environ.get(
        "PWA_STATIC_BASE",
        "https://raw.githubusercontent.com/USER/nse-trading-lab/main/static",
    )
    st.markdown(
        f'''
        <link rel="manifest" href="{base}/manifest.json">
        <meta name="theme-color" content="#00FF87">
        <script>
        if ('serviceWorker' in navigator) {{
          navigator.serviceWorker.register('{base}/service-worker.js')
            .then(reg => console.log('SW registered', reg.scope))
            .catch(err => console.warn('SW failed', err));
        }}
        </script>
        ''',
        unsafe_allow_html=True,
    )
