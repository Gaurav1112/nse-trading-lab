"""Cross-sectional momentum ranking (Asness & Moskowitz 2013, Jegadeesh-Titman 1993).

Replaces absolute-score thresholds (which produce 0 picks in HOSTILE) with
RANK-based selection across the Nifty 50 universe. The classic Jegadeesh-
Titman finding: long the top quintile by trailing-12-month return EX last
month (to avoid short-term reversal). Asness-Moskowitz validated this is
the most robust equity factor in published literature.

Why this can move the needle even in HOSTILE tape: when EVERY stock has
weak absolute trend, the engine still has to choose SOMETHING. Cross-
sectional ranking forces the decision to be "best of breed in this regime"
rather than "anything above an arbitrary threshold." On a long-only
sleeve, you're long the relative outperformers.

Important honesty: this does NOT promise to flip HOSTILE expectancy
positive. It changes the SELECTION RULE. Held-out validation should
confirm whether it lifts the bottom of the CI or just shuffles trades.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class MomentumScore:
    symbol: str
    return_12m_ex_1m_pct: float   # the actual Jegadeesh-Titman signal
    percentile_rank: float        # 0-100 across the universe
    quintile: int                 # 1 (worst) to 5 (best)
    bars_used: int


def momentum_score(df: pd.DataFrame, symbol: str = "") -> Optional[MomentumScore]:
    """Compute the 12M-ex-1M return for a single symbol.

    Requires ≥252 daily bars (~1 year + 1 month buffer). Returns None if
    insufficient data.
    """
    if df is None or len(df) < 252 or "Close" not in df.columns:
        return None
    closes = df["Close"]
    # 12-month return ex last month: ratio of (close 21 bars ago) to
    # (close ~252 bars ago) — skip the most recent month to avoid the
    # short-term reversal effect documented by Jegadeesh-Titman.
    today = closes.iloc[-1]                # noqa: F841 (used implicitly via .iloc[-21])
    one_month_ago = closes.iloc[-21]
    twelve_months_ago = closes.iloc[-252]
    if twelve_months_ago <= 0:
        return None
    ret_pct = float((one_month_ago / twelve_months_ago - 1.0) * 100)
    return MomentumScore(
        symbol=symbol,
        return_12m_ex_1m_pct=ret_pct,
        percentile_rank=0.0,            # filled in by rank_universe
        quintile=0,                      # filled in by rank_universe
        bars_used=int(min(len(df), 252)),
    )


def rank_universe(symbol_dfs: dict[str, pd.DataFrame]) -> dict[str, MomentumScore]:
    """Compute momentum scores for every symbol, then rank percentile + quintile.

    Returns {symbol: MomentumScore} with percentile_rank and quintile populated.
    """
    raw: list[MomentumScore] = []
    for sym, df in symbol_dfs.items():
        ms = momentum_score(df, symbol=sym)
        if ms is not None:
            raw.append(ms)
    if not raw:
        return {}
    raw.sort(key=lambda x: x.return_12m_ex_1m_pct)
    n = len(raw)
    out: dict[str, MomentumScore] = {}
    for i, ms in enumerate(raw):
        # Percentile rank 0..100; ties broken by sort order, which is fine
        ms.percentile_rank = round((i + 1) / n * 100, 1)
        ms.quintile = max(1, min(5, int(np.ceil((i + 1) / n * 5))))
        out[ms.symbol] = ms
    return out


def cross_sectional_boost(
    ranking: MomentumScore | None,
    *,
    top_quintile_boost: float = 5.0,
    bottom_quintile_penalty: float = 5.0,
) -> tuple[float, str]:
    """Translate a cross-sectional rank into a score adjustment.

    Returns (delta, reason). Top quintile (5) gets +boost; bottom quintile (1)
    gets -penalty; middle quintiles (2-4) are neutral. This is a SOFT signal —
    we don't downgrade verdicts purely on rank, only nudge the composite score.
    """
    if ranking is None:
        return 0.0, "Cross-sectional: insufficient history, no adjustment"
    q = ranking.quintile
    if q == 5:
        return (
            +top_quintile_boost,
            f"Cross-sectional: top quintile (rank {ranking.percentile_rank:.0f}p, "
            f"12M ex-1M return {ranking.return_12m_ex_1m_pct:+.1f}%) — "
            f"+{top_quintile_boost:.0f} score boost",
        )
    if q == 1:
        return (
            -bottom_quintile_penalty,
            f"Cross-sectional: bottom quintile (rank {ranking.percentile_rank:.0f}p, "
            f"12M ex-1M return {ranking.return_12m_ex_1m_pct:+.1f}%) — "
            f"-{bottom_quintile_penalty:.0f} score penalty",
        )
    return (
        0.0,
        f"Cross-sectional: middle quintile {q} "
        f"(rank {ranking.percentile_rank:.0f}p, return {ranking.return_12m_ex_1m_pct:+.1f}%) — neutral",
    )
