"""
Bot configuration — loads from .env and defines all constants.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Wallet / Auth ──────────────────────────────────────────────
PRIVATE_KEY: str = os.getenv("PRIVATE_KEY", "")
PROXY_ADDRESS: str = os.getenv("POLYMARKET_PROXY_ADDRESS", "")
CHAIN_ID: int = 137  # Polygon mainnet

# ── API Endpoints ──────────────────────────────────────────────
CLOB_HOST = "https://clob.polymarket.com"
GAMMA_HOST = "https://gamma-api.polymarket.com"

# ── Supported Assets ──────────────────────────────────────────
SUPPORTED_ASSETS = ["BTC", "ETH", "SOL", "XRP"]

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

MIN_EDGE: float = 0.06              # 6 cents min edge to allow more valid small-bankroll trades
MIN_TRUE_PROB: float = 0.52         # allow earlier 5m/15m entries before certainty fully builds
MAX_POSITION_PCT: float = 0.15      # 15% of bankroll per trade (needed for $100 bankroll)
KELLY_FRACTION_EARLY: float = 0.40  # 40% Kelly for early window
KELLY_FRACTION_LATE: float = 0.30   # 30% Kelly for late window (>80¢)
MAX_SIMULTANEOUS: int = 3           # max concurrent positions (lower because positions are larger)
DAILY_LOSS_LIMIT_PCT: float = 0.15  # halt if daily loss exceeds 15%
MAX_DRAWDOWN_PCT: float = 0.25      # halt if drawdown from peak > 25%

# ── Price Caps (window-specific) ──────────────────────────────
MAX_PRICE_EARLY: float = 0.35       # early window: only buy under 35¢
MAX_PRICE_LATE: float = 0.99        # late window: can buy up to 99¢
MIN_PRICE_EARLY: float = 0.40       # don't buy extreme longshots < 40¢
MIN_PRICE_LATE: float = 0.40

# ── Timing Windows ─────────────────────────────────────────────
CANDLE_DURATION_MIN: int = 15
MIN_TIME_REMAINING: float = 1.5     # don't enter within 1.5 min of close
MAX_TIME_ELAPSED_EARLY: float = 2.0 # early window closes at 2 min elapsed
LATE_WINDOW_START: float = 3.0      # late window = last 3 min of candle
SCAN_INTERVAL_SEC: int = 30         # main loop frequency

# ── Order Splitting ────────────────────────────────────────────
ORDER_SPLIT_COUNT: int = 1          # single order (no splitting needed for $5-15 trades)
ORDER_DELAY_MIN: float = 0.5       # seconds between micro-orders
ORDER_DELAY_MAX: float = 1.0

# ── Logging ────────────────────────────────────────────────────
LOG_DIR = "logs"
LOG_FILE = "bot.log"
PAPER_TRADE_FILE = "paper_trades.csv"
