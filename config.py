"""
Bot configuration — loads from .env and defines all constants.
"""

import os
from dotenv import load_dotenv

load_dotenv()


def _parse_timeframes(value: str) -> list[int]:
    """Parse comma-separated timeframe minutes (e.g. '15' or '5,15')."""
    allowed = {5, 15}
    parsed: list[int] = []
    for part in value.split(","):
        token = part.strip()
        if not token:
            continue
        try:
            tf = int(token)
        except ValueError:
            continue
        if tf in allowed and tf not in parsed:
            parsed.append(tf)
    return parsed

# ── Wallet / Auth ──────────────────────────────────────────────
PRIVATE_KEY: str = os.getenv("PRIVATE_KEY", "")
PROXY_ADDRESS: str = os.getenv("POLYMARKET_PROXY_ADDRESS", "")
CHAIN_ID: int = 137  # Polygon mainnet

# ── API Endpoints ──────────────────────────────────────────────
CLOB_HOST = "https://clob.polymarket.com"
GAMMA_HOST = "https://gamma-api.polymarket.com"

# ── Supported Assets ──────────────────────────────────────────
SUPPORTED_ASSETS = ["BTC", "ETH", "SOL", "XRP"]
ENABLED_TIMEFRAMES_MIN: list[int] = _parse_timeframes(
    os.getenv("ENABLED_TIMEFRAMES_MIN", "15")
) or [15]

# Binance WebSocket streams (kline_1m for RSI + volume)
BINANCE_WS_BASE = os.getenv("BINANCE_WS_BASE", "wss://data-stream.binance.vision:9443/stream?streams=")
BINANCE_SYMBOLS = {
    "BTC": "btcusdt",
    "ETH": "ethusdt",
    "SOL": "solusdt",
    "XRP": "xrpusdt",
}

# Chainlink on-chain price feed aggregators on Polygon
# Multiple RPCs for failover — rotates on rate limit
POLYGON_RPC_URLS: list[str] = [
    os.getenv("POLYGON_RPC_URL", "https://1rpc.io/matic"),
    "https://polygon-bor-rpc.publicnode.com",
]
POLYGON_RPC_URL = POLYGON_RPC_URLS[0]  # default for Web3 init
CHAINLINK_POLL_SEC: int = 30  # poll every 30s (oracle updates ~27s)
CHAINLINK_AGGREGATORS = {
    "BTC": "0xc907E116054Ad103354f2D350FD2514433D57F6f",
    "ETH": "0xF9680D99D6C9589e2a93a78A04A279e509205945",
    "SOL": "0x10C8264C0935b3B9870013e057f330Ff3e9C56dC",
    "XRP": "0x785ba89291f676b5386652eB12b30cF361020694",
}

# ── Risk Management ───────────────────────────────────────────
DRY_RUN: bool = os.getenv("DRY_RUN", "true").lower() == "true"
BANKROLL: float = float(os.getenv("BANKROLL", "1000.0"))
PAPER_USE_GAMMA_FALLBACK: bool = os.getenv("PAPER_USE_GAMMA_FALLBACK", "false").lower() == "true"
ENABLE_EARLY_ENTRY: bool = os.getenv("ENABLE_EARLY_ENTRY", "true").lower() == "true"

# Taker execution controls (all values are in price/probability terms, 0-1 scale)
REQUIRE_BID_FOR_ENTRY: bool = os.getenv("REQUIRE_BID_FOR_ENTRY", "true").lower() == "true"
MAX_ENTRY_SPREAD: float = float(os.getenv("MAX_ENTRY_SPREAD", "0.04"))          # 4c max bid-ask spread
TAKER_FEE_BPS: float = float(os.getenv("TAKER_FEE_BPS", "20"))                  # 20 bps default
EXECUTION_SLIPPAGE_BPS: float = float(os.getenv("EXECUTION_SLIPPAGE_BPS", "10"))# 10 bps default
LATENCY_BUFFER_MIN: float = float(os.getenv("LATENCY_BUFFER_MIN", "0.005"))     # 0.5c minimum latency/risk buffer
LATENCY_SPREAD_MULT: float = float(os.getenv("LATENCY_SPREAD_MULT", "0.5"))     # buffer grows with spread
MIN_NET_EDGE: float = float(os.getenv("MIN_NET_EDGE", "0.015"))                 # require 1.5c edge after costs

MIN_EDGE: float = float(os.getenv("MIN_EDGE", "0.045"))                 # balanced preset: 4.5 cents min modeled edge
MIN_TRUE_PROB: float = float(os.getenv("MIN_TRUE_PROB", "0.52"))
MAX_POSITION_PCT: float = float(os.getenv("MAX_POSITION_PCT", "0.10"))  # cap single trade risk to 10%
KELLY_FRACTION_EARLY: float = float(os.getenv("KELLY_FRACTION_EARLY", "0.30"))
KELLY_FRACTION_LATE: float = float(os.getenv("KELLY_FRACTION_LATE", "0.22"))
MAX_SIMULTANEOUS: int = int(os.getenv("MAX_SIMULTANEOUS", "2"))
DAILY_LOSS_LIMIT_PCT: float = float(os.getenv("DAILY_LOSS_LIMIT_PCT", "0.08"))
MAX_DRAWDOWN_PCT: float = float(os.getenv("MAX_DRAWDOWN_PCT", "0.15"))

# ── Price Caps (window-specific) ──────────────────────────────
MAX_PRICE_EARLY: float = 0.65       # early window: allow mid-range prices while avoiding near-resolution pricing
MAX_PRICE_LATE: float = 0.95        # late window: pay up to 95¢ for near-certainties
MIN_PRICE_EARLY: float = 0.30       # early window: avoid extreme longshots below 30¢
MIN_PRICE_LATE: float = 0.65        # late window: force the bot to ONLY buy heavy favorites (65¢+)

# ── Timing Windows ─────────────────────────────────────────────
CANDLE_DURATION_MIN: int = 15
MIN_TIME_REMAINING: float = float(os.getenv("MIN_TIME_REMAINING", "1.0"))   # avoid final 1 min
MAX_TIME_ELAPSED_EARLY: float = float(os.getenv("MAX_TIME_ELAPSED_EARLY", "2.0"))
LATE_WINDOW_START: float = float(os.getenv("LATE_WINDOW_START", "6.0"))      # late window starts 6 min before close
SCAN_INTERVAL_SEC: int = 30         # main loop frequency

# ── Order Splitting ────────────────────────────────────────────
ORDER_SPLIT_COUNT: int = 1          # single order (no splitting needed for $5-15 trades)
ORDER_DELAY_MIN: float = 0.5       # seconds between micro-orders
ORDER_DELAY_MAX: float = 1.0

# ── Logging ────────────────────────────────────────────────────
LOG_DIR = "logs"
LOG_FILE = "bot.log"
PAPER_TRADE_FILE = "paper_trades.csv"
