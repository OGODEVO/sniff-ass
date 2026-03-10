"""
Position sizer — fractional Kelly criterion with window-adaptive fraction.
"""

from config import BANKROLL, KELLY_FRACTION_EARLY, KELLY_FRACTION_LATE, MAX_POSITION_PCT
from logger import log


def calc_kelly_size(
    edge: float,
    market_price: float,
    true_prob: float,
    entry_window: str = "EARLY",
    bankroll: float = BANKROLL,
) -> float:
    """
    Compute position size in USD using fractional Kelly.

    Uses KELLY_FRACTION_EARLY (0.40) for early-window entries and
    KELLY_FRACTION_LATE (0.30) for late-window / high-price entries.
    """
    if market_price <= 0 or market_price >= 1:
        return 0.0

    b = (1.0 - market_price) / market_price
    if b <= 0:
        return 0.0

    full_kelly = (true_prob * b - (1.0 - true_prob)) / b
    if full_kelly <= 0:
        log.debug("[SIZER] Kelly ≤ 0 (%.4f), no bet", full_kelly)
        return 0.0

    # Window-adaptive Kelly fraction
    if entry_window == "LATE" or market_price > 0.80:
        kelly_frac = KELLY_FRACTION_LATE
    else:
        kelly_frac = KELLY_FRACTION_EARLY

    fractional = full_kelly * kelly_frac
    size_usd = bankroll * fractional
    max_size = bankroll * MAX_POSITION_PCT

    size_usd = min(size_usd, max_size)
    size_usd = round(size_usd, 2)

    log.info(
        "[SIZER] kelly=%.4f frac=%.2f(%s) size=$%.2f (cap=$%.2f)",
        full_kelly, kelly_frac, entry_window, size_usd, max_size,
    )
    return size_usd
