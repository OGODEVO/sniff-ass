"""
Risk manager — enforces position limits, drawdown, and daily loss halts.
"""

from __future__ import annotations

from config import (
    DAILY_LOSS_LIMIT_PCT,
    MAX_DRAWDOWN_PCT,
    MAX_SIMULTANEOUS,
    MIN_EDGE,
    MIN_TRUE_PROB,
)
from logger import log


class RiskManager:
    """Tracks bankroll peak, drawdown, and enforces all risk limits."""

    def __init__(self, bankroll: float) -> None:
        self.peak_bankroll = bankroll
        self.open_positions: int = 0
        self.entered_tokens: set[str] = set()

    def update_peak(self, current_bankroll: float) -> None:
        if current_bankroll > self.peak_bankroll:
            self.peak_bankroll = current_bankroll

    def check_all(
        self,
        token_id: str,
        edge: float,
        true_prob: float,
        bankroll: float,
        daily_pnl: float,
    ) -> bool:
        """Return True if trade is allowed, False if blocked by any limit."""

        # Already entered this specific position
        if token_id in self.entered_tokens:
            log.debug("[RISK] Already entered token %s", token_id[:16])
            return False

        # Daily loss limit
        if daily_pnl < 0 and abs(daily_pnl) / bankroll > DAILY_LOSS_LIMIT_PCT:
            log.warning("[RISK] 🛑 Daily loss limit hit (%.1f%%). Bot halted.",
                        abs(daily_pnl) / bankroll * 100)
            return False

        # Max drawdown from peak
        self.update_peak(bankroll + daily_pnl)  # approximate current equity
        drawdown = (self.peak_bankroll - (bankroll + daily_pnl)) / self.peak_bankroll
        if drawdown > MAX_DRAWDOWN_PCT:
            log.warning("[RISK] 🛑 Max drawdown hit (%.1f%%). Bot halted.", drawdown * 100)
            return False

        # Simultaneous positions
        if self.open_positions >= MAX_SIMULTANEOUS:
            log.debug("[RISK] Max simultaneous positions (%d) reached", MAX_SIMULTANEOUS)
            return False

        # Minimum true probability
        if true_prob < MIN_TRUE_PROB:
            log.debug("[RISK] True prob %.2f < min %.2f", true_prob, MIN_TRUE_PROB)
            return False

        # Minimum edge
        if edge < MIN_EDGE:
            log.debug("[RISK] Edge %.2f < min %.2f", edge, MIN_EDGE)
            return False

        return True

    def record_entry(self, token_id: str) -> None:
        self.open_positions += 1
        self.entered_tokens.add(token_id)


        # We don't remove from entered_tokens because we never want to re-enter
        # the exact same 15-minute token even after exit (e.g. if we somehow exited early)

    def record_exit(self) -> None:
        self.open_positions = max(0, self.open_positions - 1)
