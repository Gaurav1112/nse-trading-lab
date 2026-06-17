"""Single source of truth for engine jargon.

Used by every page to surface plain-English explanations on hover / help
icons. Keeps the explanations consistent so a term means the same thing
no matter where the user sees it.
"""
from __future__ import annotations

GLOSSARY: dict[str, str] = {
    "Brier": (
        "Mean squared error between a predicted probability and the 0/1 outcome. "
        "Lower is better. A constant predictor at the base rate scores ~0.25; "
        "the engine's held-out Brier crossed 0.25 in some folds — meaning the "
        "calibrator added no information beyond 'win rate is roughly 50%'."
    ),
    "Kelly fraction": (
        "Mathematically optimal bet size as a fraction of capital, given a win "
        "probability and reward/risk ratio. We use 1/4 Kelly (much smaller than "
        "the textbook formula) because real-world inputs are uncertain — full "
        "Kelly is too aggressive when your edge estimate could be wrong."
    ),
    "Expectancy": (
        "Average net return per trade including winners and losers. A positive "
        "expectancy means the strategy makes money on average. The engine's "
        "held-out 2026 expectancy in HOSTILE tape is -1.71% per trade."
    ),
    "Regime gate": (
        "A defensive rule that downgrades GO verdicts to WAIT when the broader "
        "Nifty 50 tape is in a HOSTILE state (below 200-EMA, falling slope). "
        "Historically this is when the engine has no measurable edge."
    ),
    "MTF disconfirmation": (
        "Multi-timeframe disconfirmation. A daily signal that says BUY but a "
        "weekly trend that says NO — we downgrade the verdict because catching "
        "a daily bounce inside a weekly downtrend tends to revert quickly."
    ),
    "R:R": (
        "Reward to risk ratio. Distance from entry to Target 1 divided by "
        "distance from entry to Stop Loss. R:R of 2.0 means you stand to gain "
        "₹2 for every ₹1 you risk."
    ),
    "IC (Information Coefficient)": (
        "Spearman rank correlation between a signal at entry and the realized "
        "trade return. IC > 0.05 is considered tradeable in academic finance. "
        "Our calibrator's IC turned negative in HOSTILE — meaning higher "
        "predicted win prob → lower actual return."
    ),
    "Deflated Sharpe": (
        "Bailey & Lopez de Prado 2014 correction to the Sharpe ratio that "
        "accounts for the number of strategy variants tested. The naive Sharpe "
        "overstates true skill when many candidates were silently discarded."
    ),
    "HOSTILE tape": (
        "Tape regime classifier output: Nifty 50 is below its 200-day EMA OR "
        "the 200-EMA is sloping down OR 60-day return is < -3%. In this regime "
        "the engine's historical edge is absent — paper-trade only."
    ),
    "Walk-forward": (
        "Backtest method where the strategy is trained on data up to time T "
        "and evaluated on T+1 onward, sliding forward in time. Prevents using "
        "future information to predict the past."
    ),
    "OOS / held-out": (
        "Out-of-sample / held-out. Data the strategy was not tuned against. "
        "Our 2026 YTD data is the only true OOS we have; 2023-2025 was used "
        "to tune gates and calibrators."
    ),
    "Time-stop": (
        "An exit rule that closes a position after N bars regardless of price, "
        "if the setup score has decayed below threshold. Stops 'bagholder' "
        "patterns where a setup goes nowhere for weeks."
    ),
    "Trail stop": (
        "A stop loss that moves UP (never down) as price advances. After Target "
        "1 hits, we move the stop to breakeven (entry price) so the trade "
        "can't become a loss from a winner."
    ),
}


def explain(term: str) -> str:
    """Return the glossary entry for `term`, or a placeholder if missing."""
    return GLOSSARY.get(term, f"_(no glossary entry for '{term}' — please file an issue)_")


def help_for(term: str) -> str:
    """Short version suitable for st.metric(help=...) or st.help."""
    full = GLOSSARY.get(term)
    if not full:
        return ""
    # First sentence only — st.metric tooltips are tiny.
    return full.split(".")[0] + "."
