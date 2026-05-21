"""
database.py
===========
SQLite persistence layer using aiosqlite with WAL mode.
All async — safe to call from the asyncio event loop.

Schema
------
virtual_orders : pending / filled / cancelled orders
trades         : completed buy/sell records with PnL
balances       : current virtual asset balances
strategy_params: persisted grid configuration
"""
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import aiosqlite

import config

logger = logging.getLogger(__name__)

# ── DDL ─────────────────────────────────────────────────────────────────────
_SCHEMA_SQL = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS virtual_orders (
    id          TEXT    PRIMARY KEY,
    side        TEXT    NOT NULL CHECK(side IN ('buy', 'sell')),
    price       REAL    NOT NULL,
    qty         REAL    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending', 'filled', 'cancelled')),
    grid_level  INTEGER,
    created_at  REAL    NOT NULL,
    updated_at  REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS trades (
    id              TEXT    PRIMARY KEY,
    order_id        TEXT    NOT NULL REFERENCES virtual_orders(id),
    side            TEXT    NOT NULL CHECK(side IN ('buy', 'sell')),
    qty             REAL    NOT NULL,
    price           REAL    NOT NULL,   -- intended price
    fill_price      REAL    NOT NULL,   -- price after slippage
    fee             REAL    NOT NULL,
    realized_pnl    REAL    NOT NULL DEFAULT 0.0,
    created_at      REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS balances (
    asset       TEXT    PRIMARY KEY,
    amount      REAL    NOT NULL DEFAULT 0.0,
    updated_at  REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS strategy_params (
    key         TEXT    PRIMARY KEY,
    value       TEXT    NOT NULL,
    updated_at  REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_orders_status ON virtual_orders(status);
CREATE INDEX IF NOT EXISTS idx_trades_created ON trades(created_at);
"""


@asynccontextmanager
async def get_db() -> AsyncIterator[aiosqlite.Connection]:
    """Async context manager providing a WAL-mode database connection."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode = WAL")
        await db.execute("PRAGMA synchronous = NORMAL")
        yield db


async def init_db() -> None:
    """Create all tables and indexes if they don't exist."""
    async with get_db() as db:
        await db.executescript(_SCHEMA_SQL)
        await db.commit()

        # Seed initial balances if not present
        now = time.time()
        await db.execute(
            "INSERT OR IGNORE INTO balances(asset, amount, updated_at) VALUES (?, ?, ?)",
            ("QUOTE", config.INITIAL_BALANCE_QUOTE, now),
        )
        await db.execute(
            "INSERT OR IGNORE INTO balances(asset, amount, updated_at) VALUES (?, ?, ?)",
            ("BASE", config.INITIAL_BALANCE_BASE, now),
        )
        await db.commit()
    logger.info("Database initialized: %s", config.DB_PATH)


async def reset_db() -> None:
    """Clear all trades, orders, strategy params, and reset balances to initial values."""
    async with get_db() as db:
        await db.execute("DELETE FROM virtual_orders")
        await db.execute("DELETE FROM trades")
        await db.execute("DELETE FROM strategy_params")
        await db.execute("DELETE FROM balances")
        now = time.time()
        await db.execute(
            "INSERT OR REPLACE INTO balances(asset, amount, updated_at) VALUES (?, ?, ?)",
            ("QUOTE", config.INITIAL_BALANCE_QUOTE, now),
        )
        await db.execute(
            "INSERT OR REPLACE INTO balances(asset, amount, updated_at) VALUES (?, ?, ?)",
            ("BASE", config.INITIAL_BALANCE_BASE, now),
        )
        await db.commit()
    logger.info("Database reset: cleared all orders, trades, params and reset balances.")


# ── Orders ───────────────────────────────────────────────────────────────────

async def save_order(order: dict[str, Any]) -> None:
    """Insert a new virtual order record."""
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO virtual_orders
                (id, side, price, qty, status, grid_level, created_at, updated_at)
            VALUES (:id, :side, :price, :qty, :status, :grid_level, :created_at, :updated_at)
            """,
            order,
        )
        await db.commit()


async def update_order_status(order_id: str, status: str) -> None:
    """Update the status of an existing order."""
    async with get_db() as db:
        await db.execute(
            "UPDATE virtual_orders SET status=?, updated_at=? WHERE id=?",
            (status, time.time(), order_id),
        )
        await db.commit()


async def get_open_orders() -> list[dict]:
    """Return all orders with status='pending'."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM virtual_orders WHERE status='pending' ORDER BY price"
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_all_orders(limit: int = 100) -> list[dict]:
    """Return recent orders for dashboard display."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM virtual_orders ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


# ── Trades ───────────────────────────────────────────────────────────────────

async def save_trade(trade: dict[str, Any]) -> None:
    """Insert a completed trade record."""
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO trades
                (id, order_id, side, qty, price, fill_price, fee, realized_pnl, created_at)
            VALUES (:id, :order_id, :side, :qty, :price, :fill_price, :fee, :realized_pnl, :created_at)
            """,
            trade,
        )
        await db.commit()


async def get_trades(limit: int = 50) -> list[dict]:
    """Return recent trades for dashboard display."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM trades ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_all_trades() -> list[dict]:
    """Return all trades (for equity curve calculation)."""
    async with get_db() as db:
        cursor = await db.execute("SELECT * FROM trades ORDER BY created_at ASC")
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


# ── Balances ─────────────────────────────────────────────────────────────────

async def get_balance(asset: str) -> float:
    """Return current balance for an asset ('QUOTE' or 'BASE')."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT amount FROM balances WHERE asset=?", (asset,)
        )
        row = await cursor.fetchone()
        return float(row["amount"]) if row else 0.0


async def update_balance(asset: str, amount: float) -> None:
    """Set balance for an asset."""
    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO balances(asset, amount, updated_at) VALUES (?, ?, ?)",
            (asset, amount, time.time()),
        )
        await db.commit()


async def get_all_balances() -> dict[str, float]:
    """Return {asset: amount} dict."""
    async with get_db() as db:
        cursor = await db.execute("SELECT asset, amount FROM balances")
        rows = await cursor.fetchall()
        return {row["asset"]: float(row["amount"]) for row in rows}


# ── Strategy params ───────────────────────────────────────────────────────────

async def save_param(key: str, value: str) -> None:
    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO strategy_params(key, value, updated_at) VALUES (?, ?, ?)",
            (key, value, time.time()),
        )
        await db.commit()


async def get_param(key: str, default: str = "") -> str:
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT value FROM strategy_params WHERE key=?", (key,)
        )
        row = await cursor.fetchone()
        return row["value"] if row else default
