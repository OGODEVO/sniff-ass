"""
Paper trading logger — simulates trades and logs to CSV for backtesting.
"""

from __future__ import annotations

import csv
import os
from datetime import datetime, timezone

from config import LOG_DIR, PAPER_TRADE_FILE
from logger import log


class PaperTrader:
    """CSV-based paper trade logger with daily reports."""

    def __init__(self, starting_bankroll: float) -> None:
        self.bankroll = starting_bankroll
        self.start_bank = starting_bankroll
        self.daily_pnl: float = 0.0
        self._trades_today: list[dict] = []
        self._log_path = os.path.join(LOG_DIR, PAPER_TRADE_FILE)

        os.makedirs(LOG_DIR, exist_ok=True)

        # Write header if file doesn't exist
        if not os.path.exists(self._log_path):
            with open(self._log_path, "w", newline="") as f:
                csv.writer(f).writerow([
                    "timestamp", "asset", "market_id", "side",
                    "market_price", "true_prob", "edge",
                    "size_usd", "shares", "entry_window",
                    "outcome", "pnl", "bankroll_after",
                ])

    def record_entry(
        self,
        asset: str,
        market_id: str,
        side: str,
        market_price: float,
        true_prob: float,
        edge: float,
        size_usd: float,
        entry_window: str,
    ) -> dict:
        """Record a paper trade entry. Outcome resolved later."""
        shares = size_usd / market_price if market_price > 0 else 0
        trade = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "asset": asset,
            "market_id": market_id,
            "side": side,
            "market_price": round(market_price, 4),
            "true_prob": round(true_prob, 4),
            "edge": round(edge, 4),
            "size_usd": round(size_usd, 2),
            "shares": round(shares, 2),
            "entry_window": entry_window,
            "outcome": "PENDING",
            "pnl": 0.0,
            "bankroll_after": round(self.bankroll, 2),
        }
        log.info(
            "[PAPER] 📝 Entry: %s %s %s | $%.2f @ %.2f (%.0f shares) | edge=%.2f",
            side, asset, entry_window, size_usd, market_price, shares, edge,
        )
        return trade

    def resolve_trade(self, trade: dict, won: bool) -> None:
        """Resolve a paper trade and log to CSV."""
        shares = trade["shares"]
        size = trade["size_usd"]

        if won:
            pnl = shares * 1.0 - size  # each share pays $1
            trade["outcome"] = "WIN"
        else:
            pnl = -size
            trade["outcome"] = "LOSE"

        trade["pnl"] = round(pnl, 2)
        self.bankroll += pnl
        self.daily_pnl += pnl
        trade["bankroll_after"] = round(self.bankroll, 2)
        self._trades_today.append(trade)

        # Append to CSV
        with open(self._log_path, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([trade[k] for k in [
                "timestamp", "asset", "market_id", "side",
                "market_price", "true_prob", "edge",
                "size_usd", "shares", "entry_window",
                "outcome", "pnl", "bankroll_after",
            ]])

        emoji = "💰" if won else "💸"
        log.info(
            "[PAPER] %s %s: %s %s | pnl=$%.2f | bankroll=$%.2f",
            emoji, trade["outcome"], trade["side"], trade["asset"],
            pnl, self.bankroll,
        )

    def daily_report(self) -> str:
        """Print and return a daily summary."""
        total = len(self._trades_today)
        if total == 0:
            report = "[PAPER] No trades today."
            log.info(report)
            return report

        wins = sum(1 for t in self._trades_today if t["outcome"] == "WIN")
        win_rate = wins / total * 100

        report = (
            f"\n{'=' * 40}\n"
            f"  DAILY PAPER TRADE REPORT\n"
            f"  Trades:       {total}\n"
            f"  Win Rate:     {win_rate:.1f}%\n"
            f"  Daily P&L:    ${self.daily_pnl:.2f}\n"
            f"  Bankroll:     ${self.bankroll:.2f}\n"
            f"  Total Return: {(self.bankroll - self.start_bank) / self.start_bank * 100:.1f}%\n"
            f"{'=' * 40}"
        )
        log.info(report)

        # Reset daily counters
        self.daily_pnl = 0.0
        self._trades_today = []
        return report
