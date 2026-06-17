"""Fractional Kelly position sizing.

Replaces the fixed `risk_pct` (e.g., 2% of capital per trade) with a
position size derived from the engine's own calibrated win probability
and the trade's reward/risk ratio. Bigger when the engine is most
confident, smaller when it's marginal — which is the whole reason we
went to the trouble of calibrating the probability in the first place.

Math:
  Kelly fraction f* = (p*b - q) / b
  where:
    p  = calibrated win probability (0-1)
    q  = 1 - p
    b  = reward / risk ratio (R:R)

Practical caveats (these matter — full Kelly is too aggressive in reality):
  - Use FRACTIONAL Kelly (default 0.25 of full Kelly). Full Kelly
    optimises log-wealth assuming the inputs are exact; ours aren't.
  - Cap maximum risk per trade at the user's configured risk_pct
    (default 2%). This is the safety floor — Kelly can never recommend
    risking MORE than the user's max risk envelope.
  - Floor at 0.25% risk per trade (a "smallest meaningful position").
    Below that, the bid-ask round-trip + brokerage eats the entire
    expected value.
  - When calibrated p <= base rate (no edge), return 0 risk → 0 shares
    → the UI surfaces this as "no Kelly edge, no position".
"""
from __future__ import annotations

from dataclasses import dataclass


_BASE_RATE = 0.50           # if calibrated p ≤ this, no edge → 0 size
_DEFAULT_KELLY_FRACTION = 0.25
_MIN_RISK_PCT = 0.25        # smallest meaningful position


@dataclass
class KellySizing:
    suggested_qty: int
    risk_pct_of_capital: float   # actual % of capital risked
    kelly_fraction: float        # full Kelly fraction (capped to safety floor)
    rationale: str               # human-readable explanation for the UI


def correlation_haircut(correlations: list[float]) -> float:
    """Multiplicative haircut applied to Kelly when adding a position that
    correlates with the existing book.

    haircut = 1 / sqrt(1 + sum(max(0, ρ)))

    Industry analog: Carver, *Systematic Trading* ch. 4; Lopez de Prado on
    correlation-aware sizing. Only positive correlations get penalised — a
    negatively-correlated diversifier shouldn't shrink the position.
    """
    if not correlations:
        return 1.0
    positive_only = [max(0.0, float(r)) for r in correlations if r is not None]
    denom = (1.0 + sum(positive_only)) ** 0.5
    return 1.0 / denom if denom > 0 else 1.0


def kelly_size(
    *,
    calibrated_win_prob_pct: float,
    risk_reward: float,
    entry_price: float,
    stop_loss: float,
    capital: float,
    max_risk_pct: float = 2.0,
    fraction: float = _DEFAULT_KELLY_FRACTION,
    open_book_correlations: list[float] | None = None,
) -> KellySizing:
    """Return KellySizing for the given setup. Never returns negative qty.

    Args:
      calibrated_win_prob_pct: calibrated probability that the trade wins,
        as a percentage (0-100). Use the v3 engine's win_probability output.
      risk_reward: R:R ratio (target gain / stop loss distance).
      entry_price, stop_loss: trade levels in INR.
      capital: total trading capital in INR.
      max_risk_pct: the user's max permitted risk per trade. Kelly cannot
        ever recommend more than this.
      fraction: fractional Kelly multiplier (default 0.25 = quarter Kelly).
    """
    p = max(0.0, min(1.0, calibrated_win_prob_pct / 100.0))
    q = 1.0 - p
    b = max(0.0, risk_reward)
    risk_per_share = max(0.0, entry_price - stop_loss)

    if b <= 0 or risk_per_share <= 0 or entry_price <= 0 or capital <= 0:
        return KellySizing(
            suggested_qty=0, risk_pct_of_capital=0.0, kelly_fraction=0.0,
            rationale="Kelly: invalid trade levels (entry/SL/R:R)",
        )

    if p <= _BASE_RATE:
        return KellySizing(
            suggested_qty=0, risk_pct_of_capital=0.0, kelly_fraction=0.0,
            rationale=(
                f"Kelly: calibrated p={p:.2%} ≤ base-rate {_BASE_RATE:.0%} "
                "— no edge, no position"
            ),
        )

    full_kelly = (p * b - q) / b
    if full_kelly <= 0:
        return KellySizing(
            suggested_qty=0, risk_pct_of_capital=0.0, kelly_fraction=0.0,
            rationale=(
                f"Kelly: full f*={full_kelly:.3f} ≤ 0 at p={p:.2%}, R:R={b:.2f}"
            ),
        )

    fractional = full_kelly * fraction          # quarter Kelly
    # Correlation haircut — shrink Kelly when the candidate correlates with
    # existing open book. Carver-style: 1 / sqrt(1 + Σmax(0,ρ)).
    haircut = correlation_haircut(open_book_correlations or [])
    fractional_after_haircut = fractional * haircut
    risk_pct = fractional_after_haircut * 100    # as percent of capital
    risk_pct = max(_MIN_RISK_PCT, min(risk_pct, max_risk_pct))
    risk_inr = capital * risk_pct / 100
    qty = max(1, int(risk_inr / risk_per_share))
    actual_risk_pct = (qty * risk_per_share) / capital * 100

    rationale = (
        f"Kelly (1/4 fractional): p_cal={p:.2%}, R:R={b:.2f} → full f*={full_kelly:.3f}"
    )
    if open_book_correlations:
        rationale += f", ρ-haircut={haircut:.3f}"
    rationale += (
        f" → sized at {risk_pct:.2f}% of capital "
        f"({qty} shares risking ₹{qty * risk_per_share:,.0f})"
    )
    return KellySizing(
        suggested_qty=qty,
        risk_pct_of_capital=actual_risk_pct,
        kelly_fraction=full_kelly,
        rationale=rationale,
    )
