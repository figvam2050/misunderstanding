"""
config.py
=========
Centralized configuration loaded from .env file.
All other modules import from here — never read os.environ directly.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root (same directory as this file)
_env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=_env_path)

# ── Exchange endpoints ──────────────────────────────────────────────────────
BASE_URL: str = os.getenv("BASE_URL", "https://whitebit.com")
WS_URL: str = os.getenv("WS_URL", "wss://api.whitebit.com/ws")

# ── Trading pair ────────────────────────────────────────────────────────────
MARKET: str = os.getenv("MARKET", "DBTC_DUSDT")

# ── Paper-trading initial balances ──────────────────────────────────────────
INITIAL_BALANCE_QUOTE: float = float(os.getenv("INITIAL_BALANCE_QUOTE", "10000.0"))
INITIAL_BALANCE_BASE: float = float(os.getenv("INITIAL_BALANCE_BASE", "0.0"))

# ── Grid parameters ─────────────────────────────────────────────────────────
GRID_LEVELS: int = int(os.getenv("GRID_LEVELS", "5"))
GRID_SPACING: float = float(os.getenv("GRID_SPACING", "200.0"))
ORDER_SIZE: float = float(os.getenv("ORDER_SIZE", "0.001"))
MIN_ORDER_SIZE: float = float(os.getenv("MIN_ORDER_SIZE", "0.00001"))
ORDER_STEP: float = float(os.getenv("ORDER_STEP", "0.000001"))

# ── Fee rates (as fractions) ─────────────────────────────────────────────────
MAKER_FEE: float = float(os.getenv("MAKER_FEE", "0.0007"))
TAKER_FEE: float = float(os.getenv("TAKER_FEE", "0.00095"))

# ── Slippage (fraction of price) ────────────────────────────────────────────
SLIPPAGE_PCT: float = float(os.getenv("SLIPPAGE_PCT", "0.0003"))

# ── Data freshness ───────────────────────────────────────────────────────────
DATA_STALE_SECONDS: float = float(os.getenv("DATA_STALE_SECONDS", "30.0"))

# ── Logging ──────────────────────────────────────────────────────────────────
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FILE: str = os.getenv("LOG_FILE", "bot_dca.log")

# ── Database ─────────────────────────────────────────────────────────────────
DB_PATH: str = os.getenv("DB_PATH", str(Path(__file__).parent / "paper_trading.db"))

# ── WebSocket reconnect limits ───────────────────────────────────────────────
WS_RECONNECT_MIN_WAIT: float = 1.0
WS_RECONNECT_MAX_WAIT: float = 60.0
