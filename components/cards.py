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


def zerodha_steps_intraday(sym: str, entry: float, sl: float, shares: int,
                            target: float = 0) -> str:
    """Intraday-specific Zerodha workflow: MIS product (intraday margin,
    auto-square-off at 15:15 IST) + Cover Order (CO) or SL-Limit so SL is
    placed atomically with entry. CRITICAL: do NOT use CNC for intraday —
    CNC means delivery (full cash, T+1 settlement, NO auto-square-off, you'd
    be holding overnight with gap risk).
    """
    rr_text = f" → target ₹{target:,.2f}" if target > 0 else ""
    return (
        f'<div class="steps" style="border-left:4px solid #00D4FF;'
        f'background:#0a1a2a;padding:14px 18px;border-radius:10px">'
        f'<b>⚡ Intraday on Zerodha Kite (MIS + Cover Order)</b>'
        f'<div style="font-size:12px;color:#8DD7FF;margin:6px 0">'
        f'<b>Product = MIS</b> (intraday margin, auto-squareoff 15:15 IST). '
        f'<b>Cover Order</b> places SL atomically with entry — Kite\'s safest '
        f'intraday wrapper.'
        f'</div>'
        f'<ol>'
        f'<li>Open Kite → Search <b>{sym}</b></li>'
        f'<li>Tap <b>B</b> → Product: <b>MIS</b> → Type: <b>CO</b> (Cover Order)</li>'
        f'<li>Buy price: <b>LIMIT ₹{entry:,.2f}</b> · Qty: <b>{shares}</b></li>'
        f'<li>SL trigger: <b>₹{sl:,.2f}</b>{rr_text}</li>'
        f'<li>Submit. CO places both legs atomically.</li>'
        '</ol>'
        '<span style="color:#FF9050;font-size:13px">'
        '⚠️ MIS auto-squares-off at 15:15 IST. Close manually before that '
        '(or your position fills at the worst price of the day). '
        'For target: place a separate LIMIT sell order after fill.</span>'
        '</div>'
    )


def zerodha_steps(sym: str, entry: float, sl: float, shares: int,
                  target_1: float = 0, target_2: float = 0,
                  mode: str = "SWING") -> str:
    """Complete BUY workflow. Branches by mode:

      - SWING / POSITIONAL / LONGTERM → CNC + LIMIT + SL-M + T1/T2 GTT.
        Holds overnight (delivery). T+1 settlement. STT 0.1% sell-only.

      - INTRADAY → MIS + CO (Cover Order). Auto-squared at 15:15 IST.
        STT 0.025%. Intraday margin (5x). No T1/T2 GTT — book targets manually
        because CO already binds the SL atomically.

    The CNC default existed for swing trades. Calling this with mode="INTRADAY"
    on an intraday signal previously emitted CNC steps — that bug forced the
    user to hold an intraday loss overnight as a delivery position. This
    function now refuses to be misused.
    """
    if mode.upper() == "INTRADAY":
        return zerodha_steps_intraday(sym, entry, sl, shares, target=target_1)
    half = max(1, shares // 2)
    rest = max(1, shares - half)
    base = (
        f'<div class="steps"><b>📱 Execute on Zerodha Kite</b>'
        f'<ol>'
        f'<li>Open Kite → Search <b>{sym}</b></li>'
        f'<li>Tap <b>B</b> → Product: <b>CNC</b> → Type: <b>LIMIT</b> → '
        f'Price <b>₹{entry:,.2f}</b> · Qty <b>{shares}</b></li>'
        f'<li>After fill → Place <b>SL-M</b> sell at <b>₹{sl:,.2f}</b> · Qty <b>{shares}</b></li>'
    )
    if target_1 > 0:
        base += (
            f'<li>Place <b>GTT (Single)</b> sell <b>LIMIT ₹{target_1:,.2f}</b> · '
            f'Qty <b>{half}</b> (book 50% at T1)</li>'
        )
    if target_2 > 0:
        base += (
            f'<li>Place <b>GTT (Single)</b> sell <b>LIMIT ₹{target_2:,.2f}</b> · '
            f'Qty <b>{rest}</b> (book remaining at T2)</li>'
        )
    base += (
        '</ol>'
        '<span style="color:#FFB800;font-size:13px">'
        '⚡ Set SL-M immediately after buy. When T1 GTT triggers, '
        'manually move the SL-M trigger up to entry (breakeven trail).</span>'
        '</div>'
    )
    return base


def zerodha_sell_steps(sym: str, current_price: float, shares_held: int,
                       reason: str = "") -> str:
    """SELL-NOW workflow when daily_check returns EXIT.

    The engine is recommending you close the position. Concrete steps to
    sell at market or limit, with the reason surfaced inline.
    """
    return (
        f'<div class="steps" style="border-left:4px solid #FF4D4D;'
        f'background:#1a0d0d;padding:14px 18px;border-radius:10px"><b>'
        f'🔴 SELL — exit position now</b>'
        + (f'<div style="font-size:13px;color:#FFB0B0;margin:6px 0">{reason}</div>' if reason else '')
        + f'<ol style="margin-top:8px">'
        f'<li>Open Kite → Search <b>{sym}</b></li>'
        f'<li>Cancel any open SL-M / GTT sell orders on this symbol first</li>'
        f'<li>Tap <b>S</b> → Product: <b>CNC</b> → Type: <b>MARKET</b> → '
        f'Qty <b>{shares_held}</b></li>'
        f'<li>Confirm. Realised fill should be near current ₹{current_price:,.2f}.</li>'
        '</ol>'
        '<span style="color:#FFA0A0;font-size:13px">'
        "Don't argue with the engine: it has re-scored the setup against today's "
        'tape and finds the thesis no longer holds.</span>'
        '</div>'
    )


def zerodha_modify_sl_steps(sym: str, old_sl: float, new_sl: float,
                            shares_held: int) -> str:
    """When daily_check returns TIGHTEN_STOP: concrete Kite steps to MODIFY
    the existing SL-M sell order to the new (tighter) trigger price.
    """
    move = new_sl - old_sl
    pct = (move / old_sl * 100) if old_sl > 0 else 0
    return (
        f'<div class="steps" style="border-left:4px solid #FFB800;'
        f'background:#1a1408;padding:14px 18px;border-radius:10px"><b>'
        f'⚠️ TIGHTEN STOP — move SL up</b>'
        f'<div style="font-size:13px;color:#FFE0A0;margin:6px 0">'
        f'Score is slipping. Lock in part of the open profit by moving SL up '
        f'<b>₹{old_sl:,.2f} → ₹{new_sl:,.2f}</b> ({move:+.2f} INR, {pct:+.2f}%).'
        f'</div><ol>'
        f'<li>Open Kite → Orders → Open Orders tab</li>'
        f'<li>Find the SL-M sell order on <b>{sym}</b> (Qty {shares_held})</li>'
        f'<li>Tap <b>Modify</b> → change <b>Trigger Price</b> to <b>₹{new_sl:,.2f}</b></li>'
        f'<li>Confirm. The order should now show the new trigger.</li>'
        '</ol>'
        '<span style="color:#FFB800;font-size:13px">'
        'Never widen a stop — only tighten. If the price has already moved '
        'past the suggested new SL, exit at market instead.</span>'
        '</div>'
    )
