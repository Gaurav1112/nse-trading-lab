def score_bar(label: str, val: float, tip: str = "") -> str:
    p = min(val / 100, 1.0)
    c = "#00FF87" if p >= 0.65 else "#FFB800" if p >= 0.45 else "#FF3355"
    t = f' title="{tip}"' if tip else ""
    return (
        f'<div style="margin:6px 0"{t}>'
        f'<div style="display:flex;justify-content:space-between;font-size:11px;color:#5A7390;'
        f'margin-bottom:3px;text-transform:uppercase;letter-spacing:.05em">'
        f'<span>{label}</span>'
        f'<span style="color:{c};font-weight:600;font-family:JetBrains Mono,monospace">{val:.0f}</span>'
        f'</div><div style="background:#1A2540;border-radius:6px;height:6px;overflow:hidden">'
        f'<div style="width:{p*100:.0f}%;background:{c};height:100%;border-radius:6px;'
        f'transition:width .5s ease"></div></div></div>'
    )


def score_gauge(val: float, verdict: str) -> str:
    pct = f"{val / 100 * 360:.0f}deg"
    cls = "gauge-go" if verdict == "GO" else "gauge-wait" if verdict == "WAIT" else "gauge-avoid"
    return (
        f'<div style="display:flex;flex-direction:column;align-items:center;gap:6px">'
        f'<div class="gauge {cls}" style="--pct:{pct}">'
        f'<div style="background:#050A14;width:70px;height:70px;border-radius:50%;'
        f'display:flex;align-items:center;justify-content:center">{val:.0f}</div></div>'
        f'<span style="font-size:12px;color:#5A7390;text-transform:uppercase;'
        f'letter-spacing:.1em">{verdict}</span></div>'
    )


def verdict_card(verdict: str, score: float, reasons: list[str]) -> str:
    cls = "verdict-go" if verdict == "GO" else "verdict-wait" if verdict == "WAIT" else "verdict-avoid"
    icon = "🟢" if verdict == "GO" else "🟡" if verdict == "WAIT" else "🔴"
    items = "".join(
        f'<li style="font-size:13px;margin:4px 0;color:#7A93AA;text-align:left">{r}</li>'
        for r in reasons[:6]
    )
    return (
        f'<div class="{cls}">{icon} <strong>{verdict}</strong> — {score:.0f}/100'
        f'<ul style="text-align:left;margin:12px 0 0;padding-left:18px">{items}</ul></div>'
    )


def metric_card(label: str, value: str, delta: str | None = None, color: str | None = None) -> str:
    color = color or "#4D9FFF"
    delta_html = ""
    if delta is not None:
        dc = "#00FF87" if delta.startswith("+") else "#FF3355" if delta.startswith("-") else "#5A7390"
        delta_html = f'<div style="font-size:12px;color:{dc};margin-top:4px">{delta}</div>'
    return (
        f'<div style="background:#0D1526;padding:12px 16px;border-radius:10px;'
        f'border:1px solid #1E3A5F;border-left:3px solid {color};margin:4px 0">'
        f'<div style="font-size:11px;color:#5A7390;text-transform:uppercase;'
        f'letter-spacing:.05em;margin-bottom:4px">{label}</div>'
        f'<div style="font-size:22px;font-family:JetBrains Mono,monospace;'
        f'color:#E8EDF5;font-weight:700">{value}</div>{delta_html}</div>'
    )


def warning_card(text: str) -> str:
    return (
        f'<div style="background:#0D1206;padding:12px 16px;border-radius:8px;'
        f'border-left:3px solid #FFB800;margin:6px 0;font-size:14px">⚠️ {text}</div>'
    )


def zerodha_steps(sym: str, entry: float, sl: float, shares: int) -> str:
    return (
        f'<div class="steps"><b>📱 Execute on Zerodha Kite</b>'
        f'<ol><li>Open Kite → Search <b>{sym}</b></li>'
        f'<li>Tap <b>B</b> → Product: <b>CNC</b> → Type: <b>LIMIT</b></li>'
        f'<li>Price: <b>₹{entry:,.0f}</b> | Qty: <b>{shares}</b></li>'
        f'<li>After fill → Place <b>SL-M</b> sell at <b>₹{sl:,.0f}</b></li></ol>'
        f'<span style="color:#FFB800;font-size:13px">⚡ Always set SL-M immediately after buying</span></div>'
    )
