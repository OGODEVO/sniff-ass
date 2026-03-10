"""
Market scanner — discovers active 5-min and 15-min Up/Down crypto markets via slug lookup.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

from config import GAMMA_HOST, SUPPORTED_ASSETS
from logger import log

# Slug pattern: btc-updown-5m-1773067500 / btc-updown-15m-1773067500
_SLUG_PATTERN = re.compile(r"^(btc|eth|sol|xrp)-updown-(5|15)m-(\d+)$")

# Map slug prefix → our asset name
_SLUG_ASSET_MAP = {
    "btc": "BTC",
    "eth": "ETH",
    "sol": "SOL",
    "xrp": "XRP",
}


def _parse_json_field(value, default=None):
    """Parse a field that may be a JSON string, list, or None."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
    return default if default is not None else []


@dataclass
class ActiveMarket:
    """Parsed representation of a live Up/Down market."""

    condition_id: str
    question: str
    slug: str
    token_id_up: str
    token_id_down: str
    outcome_price_up: float
    outcome_price_down: float
    end_time: datetime
    event_start_time: datetime
    minutes_elapsed: float
    time_remaining: float
    asset: str
    timeframe_min: int


def _build_candidate_slugs() -> list[str]:
    """
    Generate candidate slugs for the current, previous, and next 5m/15m windows.
    """
    now = int(time.time())
    slugs = []

    aligned_15m = (now // 900) * 900
    for offset in [-900, 0, 900, 1800]:
        ts = aligned_15m + offset
        for asset_lower in ["btc", "eth", "sol", "xrp"]:
            asset_upper = _SLUG_ASSET_MAP[asset_lower]
            if asset_upper in SUPPORTED_ASSETS:
                slugs.append(f"{asset_lower}-updown-15m-{ts}")

    aligned_5m = (now // 300) * 300
    for offset in [-300, 0, 300, 600]:
        ts = aligned_5m + offset
        for asset_lower in ["btc", "eth", "sol", "xrp"]:
            asset_upper = _SLUG_ASSET_MAP[asset_lower]
            if asset_upper in SUPPORTED_ASSETS:
                slugs.append(f"{asset_lower}-updown-5m-{ts}")

    return slugs


async def _fetch_event_by_slug(client: httpx.AsyncClient, slug: str) -> dict | None:
    """Fetch a single event from Gamma by slug."""
    try:
        resp = await client.get(
            f"{GAMMA_HOST}/events",
            params={"slug": slug},
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list) and len(data) > 0:
            return data[0]
        return None
    except Exception as exc:
        log.debug("[SCANNER] Slug fetch failed %s: %s", slug, exc)
        return None


def _parse_market(event: dict, asset: str) -> ActiveMarket | None:
    """Parse an event dict into an ActiveMarket."""
    markets = event.get("markets", [])
    if not markets:
        return None

    m = markets[0]
    event_tokens = event.get("tokens", [])
    now = datetime.now(timezone.utc)

    slug_str = m.get("slug", "")
    timeframe_min = 5 if "-updown-5m-" in slug_str else 15

    # Parse end time
    end_str = m.get("endDate", "")
    if not end_str:
        return None
    try:
        end_time = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
    except ValueError:
        return None

    # Parse event start time (candle open)
    start_str = m.get("eventStartTime", "")
    try:
        event_start = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        event_start = end_time - timedelta(minutes=timeframe_min)

    remaining_sec = (end_time - now).total_seconds()
    elapsed_sec = (now - event_start).total_seconds()
    max_elapsed_sec = timeframe_min * 60

    # Skip resolved or far-future markets
    if remaining_sec <= 0 or elapsed_sec < 0 or elapsed_sec > max_elapsed_sec:
        return None

    time_remaining = remaining_sec / 60
    minutes_elapsed = elapsed_sec / 60

    # Parse stringified JSON fields
    outcomes = _parse_json_field(m.get("outcomes"), ["Up", "Down"])
    outcome_prices = _parse_json_field(m.get("outcomePrices"), ["0.5", "0.5"])
    clob_tokens = _parse_json_field(m.get("clobTokenIds"), [])

    price_up = 0.5
    price_down = 0.5
    for i, label in enumerate(outcomes):
        if i < len(outcome_prices):
            if str(label).lower() in ("up", "yes"):
                price_up = float(outcome_prices[i])
            elif str(label).lower() in ("down", "no"):
                price_down = float(outcome_prices[i])

    # Extract token IDs from clobTokenIds (order matches outcomes)
    token_id_up = ""
    token_id_down = ""

    if len(clob_tokens) >= 2:
        for i, label in enumerate(outcomes):
            if i < len(clob_tokens):
                if str(label).lower() in ("up", "yes"):
                    token_id_up = str(clob_tokens[i])
                elif str(label).lower() in ("down", "no"):
                    token_id_down = str(clob_tokens[i])

    # Method 2: tokens array with outcome field
    if not token_id_up or not token_id_down:
        for tok in event_tokens:
            if isinstance(tok, dict):
                outcome = tok.get("outcome", "").lower()
                tid = tok.get("token_id", "")
                if outcome in ("up", "yes"):
                    token_id_up = tid
                elif outcome in ("down", "no"):
                    token_id_down = tid

    if not token_id_up or not token_id_down:
        log.warning("[SCANNER] No token IDs found for %s", m.get("slug", ""))
        return None

    return ActiveMarket(
        condition_id=m.get("conditionId", m.get("questionID", "")),
        question=m.get("question", ""),
        slug=slug_str,
        token_id_up=token_id_up,
        token_id_down=token_id_down,
        outcome_price_up=price_up,
        outcome_price_down=price_down,
        end_time=end_time,
        event_start_time=event_start,
        minutes_elapsed=round(minutes_elapsed, 2),
        time_remaining=round(time_remaining, 2),
        asset=asset,
        timeframe_min=timeframe_min,
    )


async def scan_active_markets() -> list[ActiveMarket]:
    """
    Discover active 5-min and 15-min crypto markets via slug-based lookup.
    Constructs slugs from aligned timestamps, fetches from Gamma events API.
    """
    slugs = _build_candidate_slugs()
    results: list[ActiveMarket] = []
    seen_slugs: set[str] = set()

    async with httpx.AsyncClient(timeout=15) as client:
        for slug in slugs:
            # Extract asset from slug
            match = _SLUG_PATTERN.match(slug)
            if not match:
                continue
            asset_lower = match.group(1)
            asset = _SLUG_ASSET_MAP[asset_lower]

            event = await _fetch_event_by_slug(client, slug)
            if not event:
                continue

            market = _parse_market(event, asset)
            if market and market.slug not in seen_slugs:
                seen_slugs.add(market.slug)
                results.append(market)

    if results:
        log.info("[SCANNER] Found %d active 5m/15m markets", len(results))
        for r in results:
            log.info(
                "[SCANNER]   %s %sm %s | elapsed=%.1fm rem=%.1fm | up=%.2f down=%.2f",
                r.asset, r.timeframe_min, r.slug, r.minutes_elapsed, r.time_remaining,
                r.outcome_price_up, r.outcome_price_down,
            )
    else:
        log.debug("[SCANNER] No active 5m/15m markets found (checked %d slugs)", len(slugs))

    return results
