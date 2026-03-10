"""
Candle tracker — tracks timeframe-specific Polymarket candle opens per asset.
"""

from __future__ import annotations

from datetime import datetime, timezone

from logger import log


class CandleTracker:
    """Tracks candle open prices and computes elapsed/remaining time per asset."""

    _SUPPORTED_TIMEFRAMES = (5, 15)

    def __init__(self) -> None:
        # {("BTC", 5): {"open_time": datetime, "open_price": float}}
        self._candle_opens: dict[tuple[str, int], dict] = {}
        # {"BTC": 87432.50}
        self._current_prices: dict[str, float] = {}

    @staticmethod
    def get_candle_open_time(now: datetime | None = None, timeframe_min: int = 15) -> datetime:
        """
        Returns the most recent candle open time for the given timeframe.
        """
        if now is None:
            now = datetime.now(timezone.utc)
        candle_minute = (now.minute // timeframe_min) * timeframe_min
        return now.replace(minute=candle_minute, second=0, microsecond=0)

    def update_price(self, asset: str, price: float) -> None:
        """
        Update the latest price for an asset.
        If a new candle has started for a supported timeframe, record this as the open price.
        """
        self._current_prices[asset] = price
        now = datetime.now(timezone.utc)

        for timeframe_min in self._SUPPORTED_TIMEFRAMES:
            key = (asset, timeframe_min)
            candle_open_time = self.get_candle_open_time(now, timeframe_min)

            if (
                key not in self._candle_opens
                or self._candle_opens[key]["open_time"] < candle_open_time
            ):
                self._candle_opens[key] = {
                    "open_time": candle_open_time,
                    "open_price": price,
                }
                log.info(
                    "[CANDLE] New %sm candle for %s | open=$%.2f at %s",
                    timeframe_min, asset, price, candle_open_time.strftime("%H:%M"),
                )

    def get_current_price(self, asset: str) -> float:
        """Return the latest known price for an asset."""
        return self._current_prices.get(asset, 0.0)

    def has_candle(self, asset: str, timeframe_min: int = 15) -> bool:
        """Whether we have candle data for this asset and timeframe."""
        return (asset, timeframe_min) in self._candle_opens and asset in self._current_prices

    def get_candle_data(self, asset: str, timeframe_min: int = 15) -> dict | None:
        """
        Return candle state for an asset:
        {open_price, current_price, price_delta_pct, elapsed_minutes, remaining_minutes}
        """
        if not self.has_candle(asset, timeframe_min):
            return None

        now = datetime.now(timezone.utc)
        key = (asset, timeframe_min)
        open_time = self._candle_opens[key]["open_time"]
        open_price = self._candle_opens[key]["open_price"]
        current = self._current_prices[asset]

        elapsed_sec = (now - open_time).total_seconds()
        elapsed_min = elapsed_sec / 60.0
        remaining_min = timeframe_min - elapsed_min

        if open_price <= 0 or remaining_min < 0:
            return None

        return {
            "open_price": open_price,
            "current_price": current,
            "price_delta_pct": (current - open_price) / open_price,
            "elapsed_minutes": round(elapsed_min, 2),
            "remaining_minutes": round(remaining_min, 2),
        }
