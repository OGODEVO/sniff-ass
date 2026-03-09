"""
Technical indicators — RSI and volume ratio from Binance 1-min klines.
"""

from __future__ import annotations

from collections import deque

from logger import log


class Indicators:
    """Maintains rolling 1-min kline data per asset and computes indicators."""

    def __init__(self, rsi_period: int = 14, volume_period: int = 20) -> None:
        self._rsi_period = rsi_period
        self._volume_period = volume_period
        # Per-asset rolling close prices and volumes
        self._closes: dict[str, deque[float]] = {}
        self._volumes: dict[str, deque[float]] = {}

    def _ensure_asset(self, asset: str) -> None:
        if asset not in self._closes:
            max_len = max(self._rsi_period, self._volume_period) + 5
            self._closes[asset] = deque(maxlen=max_len)
            self._volumes[asset] = deque(maxlen=max_len)

    def update_kline(self, asset: str, close: float, volume: float) -> None:
        """Feed a completed 1-minute kline close price and volume."""
        self._ensure_asset(asset)
        self._closes[asset].append(close)
        self._volumes[asset].append(volume)

    # ── RSI ────────────────────────────────────────────────────

    def get_rsi(self, asset: str) -> float:
        """
        Calculate RSI from rolling 1-min close prices.
        Returns 50.0 (neutral) if not enough data.
        """
        self._ensure_asset(asset)
        prices = list(self._closes[asset])
        period = self._rsi_period

        if len(prices) < period + 1:
            return 50.0

        deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
        recent = deltas[-period:]

        gains = [d for d in recent if d > 0]
        losses = [-d for d in recent if d < 0]

        avg_gain = sum(gains) / period if gains else 0.0
        avg_loss = sum(losses) / period if losses else 0.0

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))
        return round(rsi, 2)

    # ── Volume Ratio ───────────────────────────────────────────

    def get_volume_ratio(self, asset: str) -> float:
        """
        Current volume / average of last `volume_period` candles.
        Returns 1.0 (neutral) if not enough data.
        """
        self._ensure_asset(asset)
        vols = list(self._volumes[asset])
        period = self._volume_period

        if len(vols) < 2:
            return 1.0

        current = vols[-1]
        history = vols[-(period + 1):-1] if len(vols) > period else vols[:-1]

        if not history:
            return 1.0

        avg = sum(history) / len(history)
        if avg == 0:
            return 1.0

        return round(current / avg, 2)

    def get_momentum(self, asset: str, lookback: int = 3) -> float:
        """
        Rate of price change over last `lookback` 1-min candles.
        Returns 0.0 if not enough data.
        """
        self._ensure_asset(asset)
        prices = list(self._closes[asset])
        if len(prices) < lookback + 1:
            return 0.0
        old = prices[-(lookback + 1)]
        new = prices[-1]
        if old == 0:
            return 0.0
        return round((new - old) / old, 6)
