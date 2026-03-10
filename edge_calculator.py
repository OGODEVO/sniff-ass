"""
Edge calculator — estimates true probability, computes edge, and decides whether to trade.

Uses the V2 formula from the March 2026 research paper:
  direction_strength = price_delta_pct * 80
  certainty_boost    = direction_strength * time_weight * 0.5
  raw_prob_up        = 0.50 + direction_strength + certainty_boost
"""

from __future__ import annotations

from dataclasses import dataclass

from config import (
    MAX_PRICE_EARLY,
    MAX_PRICE_LATE,
    MIN_PRICE_EARLY,
    MIN_PRICE_LATE,
    MAX_TIME_ELAPSED_EARLY,
    MIN_EDGE,
    MIN_TIME_REMAINING,
    LATE_WINDOW_START,
)
from logger import log


# ── Maximum edge sanity check ─────────────────────────────────
# If our model diverges from the crowd by more than this, assume MODEL error
MAX_EDGE: float = 0.45

# ── Correlation limit ─────────────────────────────────────────
# Max assets betting the same direction in one scan cycle
MAX_SAME_DIRECTION: int = 3

# Track how many trades fired in each direction per scan cycle
_cycle_directions: dict[str, int] = {}


def reset_cycle_directions() -> None:
    """Call at the start of each bot_loop scan cycle."""
    global _cycle_directions
    _cycle_directions = {"UP": 0, "DOWN": 0}


@dataclass
class MarketSignal:
    """Full signal object for a single market."""

    asset: str
    market_id: str
    token_id_up: str
    token_id_down: str
    candle_open_price: float
    current_price: float
    price_delta_pct: float
    time_elapsed_min: float
    time_remaining_min: float
    rsi_1min: float
    volume_ratio: float
    momentum: float
    true_prob_up: float
    true_prob_down: float
    market_price_up: float
    market_price_down: float
    edge_up: float
    edge_down: float
    recommended_side: str     # "UP", "DOWN", or "SKIP"
    recommended_size: float   # from Kelly (filled later)
    entry_window: str         # "EARLY", "LATE", or "DEAD"


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def calc_true_probability(
    candle_open_price: float,
    current_price: float,
    time_elapsed_min: float,
    timeframe_min: int = 15,
) -> tuple[float, float]:
    """
    V2 probability formula:
      direction_strength = pct_move * 80   (each 0.1% ≈ 8% prob shift)
      certainty_boost    = direction_strength * time_weight * 0.5
      raw_prob_up        = 0.50 + direction_strength + certainty_boost
    """
    if candle_open_price <= 0:
        return 0.50, 0.50

    price_delta_pct = (current_price - candle_open_price) / candle_open_price
    time_weight = time_elapsed_min / timeframe_min  # 0.0 at start → 1.0 at end

    direction_strength = price_delta_pct * 80
    certainty_boost = direction_strength * time_weight * 0.5

    raw_prob_up = 0.50 + direction_strength + certainty_boost
    prob_up = clamp(raw_prob_up, 0.01, 0.99)
    prob_down = 1.0 - prob_up

    return round(prob_up, 4), round(prob_down, 4)


def _get_entry_window(elapsed: float, remaining: float, timeframe_min: int = 15) -> str:
    """Classify the current timing window."""
    scale = timeframe_min / 15.0
    if remaining < MIN_TIME_REMAINING * scale:
        return "DEAD"  # too close to resolution
    if elapsed <= MAX_TIME_ELAPSED_EARLY * scale:
        return "EARLY"
    if remaining <= LATE_WINDOW_START * scale:
        return "LATE"
    return "DEAD"  # minutes 3–12, avoid


def build_signal(
    asset: str,
    market_id: str,
    token_id_up: str,
    token_id_down: str,
    candle_open_price: float,
    current_price: float,
    elapsed_min: float,
    remaining_min: float,
    market_price_up: float,
    market_price_down: float,
    rsi: float,
    volume_ratio: float,
    momentum: float,
    timeframe_min: int = 15,
) -> MarketSignal:
    """Build a complete MarketSignal with edge computation."""
    prob_up, prob_down = calc_true_probability(
        candle_open_price,
        current_price,
        elapsed_min,
        timeframe_min=timeframe_min,
    )
    edge_up = prob_up - market_price_up
    edge_down = prob_down - market_price_down
    price_delta_pct = (
        (current_price - candle_open_price) / candle_open_price
        if candle_open_price > 0 else 0.0
    )
    window = _get_entry_window(elapsed_min, remaining_min, timeframe_min=timeframe_min)

    return MarketSignal(
        asset=asset,
        market_id=market_id,
        token_id_up=token_id_up,
        token_id_down=token_id_down,
        candle_open_price=candle_open_price,
        current_price=current_price,
        price_delta_pct=price_delta_pct,
        time_elapsed_min=elapsed_min,
        time_remaining_min=remaining_min,
        rsi_1min=rsi,
        volume_ratio=volume_ratio,
        momentum=momentum,
        true_prob_up=prob_up,
        true_prob_down=prob_down,
        market_price_up=market_price_up,
        market_price_down=market_price_down,
        edge_up=edge_up,
        edge_down=edge_down,
        recommended_side="SKIP",
        recommended_size=0.0,
        entry_window=window,
    )


def should_trade(signal: MarketSignal) -> tuple[bool, str, float, float, float]:
    """
    Entry decision logic with RSI + volume confirmation.

    Returns (should_enter, side, edge, market_price, true_prob)
    """
    # Rule 1: Only trade in edge windows
    if signal.entry_window == "DEAD":
        return False, "SKIP", 0, 0, 0

    # Rule 2: Pick best side by edge
    if signal.edge_up >= signal.edge_down:
        side = "UP"
        edge = signal.edge_up
        market_price = signal.market_price_up
        true_prob = signal.true_prob_up
    else:
        side = "DOWN"
        edge = signal.edge_down
        market_price = signal.market_price_down
        true_prob = signal.true_prob_down

    # Rule 3: Minimum edge
    if edge < MIN_EDGE:
        return False, "SKIP", 0, 0, 0

    # Rule 4: REQUIRE valid indicators — skip if Binance WS is disconnected
    # RSI=50.0 + vol=1.0 are the defaults when no data exists
    if signal.rsi_1min == 50.0 and signal.volume_ratio == 1.0:
        log.warning(
            "[EDGE] ⚠️ Indicators are default (RSI=50, vol=1.0) — Binance WS likely down. SKIPPING %s %s",
            side, signal.asset,
        )
        return False, "SKIP", 0, 0, 0

    # Rule 5: RSI confirmation
    if side == "UP" and signal.rsi_1min < 40:
        log.debug("[EDGE] RSI %.1f contradicts UP bet, skipping", signal.rsi_1min)
        return False, "SKIP", 0, 0, 0
    if side == "DOWN" and signal.rsi_1min > 60:
        log.debug("[EDGE] RSI %.1f contradicts DOWN bet, skipping", signal.rsi_1min)
        return False, "SKIP", 0, 0, 0

    # Rule 6: Volume confirmation (early window only)
    if signal.entry_window == "EARLY" and signal.volume_ratio < 0.8:
        log.debug("[EDGE] Low volume ratio %.2f in early window, skipping", signal.volume_ratio)
        return False, "SKIP", 0, 0, 0

    # Rule 7: Max and Min price per window — block extreme longshots
    max_price = MAX_PRICE_EARLY if signal.entry_window == "EARLY" else MAX_PRICE_LATE
    min_price = MIN_PRICE_EARLY if signal.entry_window == "EARLY" else MIN_PRICE_LATE
    if market_price > max_price:
        log.debug("[EDGE] Price %.2f > max %.2f for %s window", market_price, max_price, signal.entry_window)
        return False, "SKIP", 0, 0, 0
    if market_price < min_price:
        log.debug("[EDGE] Price %.2f < min %.2f — extreme longshot blocked", market_price, min_price)
        return False, "SKIP", 0, 0, 0

    # Rule 8: Edge sanity cap — if model disagrees with crowd by >45¢, model is wrong
    if edge > MAX_EDGE:
        log.warning(
            "[EDGE] ⚠️ Edge %.2f > max %.2f for %s %s — model likely wrong, SKIPPING",
            edge, MAX_EDGE, side, signal.asset,
        )
        return False, "SKIP", 0, 0, 0

    # Rule 9: Correlation block — max 3 assets in the same direction per cycle
    dir_count = _cycle_directions.get(side, 0)
    if dir_count >= MAX_SAME_DIRECTION:
        log.warning(
            "[EDGE] ⚠️ Already %d %s bets this cycle — correlation block, SKIPPING %s",
            dir_count, side, signal.asset,
        )
        return False, "SKIP", 0, 0, 0
    _cycle_directions[side] = dir_count + 1

    log.info(
        "[EDGE] ✅ Signal: %s %s | window=%s | prob=%.2f mkt=%.2f edge=%.2f | RSI=%.1f vol=%.2f",
        side, signal.asset, signal.entry_window, true_prob, market_price, edge,
        signal.rsi_1min, signal.volume_ratio,
    )

    return True, side, edge, market_price, true_prob
