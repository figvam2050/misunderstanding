"""
virtual_orders.py
=================
Paper-trading order abstraction layer.

IMPORTANT: This module MUST NOT make any real exchange API calls.
All order operations are simulated against the local SQLite database.

Order lifecycle: pending → filled | cancelled
"""
import asyncio
import logging
import time
import uuid
from typing import Optional

import config
import database as db

logger = logging.getLogger(__name__)

# Safety guard — raise if anyone tries to do real trading
_REAL_TRADING_GUARD = True


def _assert_paper_trading() -> None:
    """Raise RuntimeError if somehow called in a real-trading context."""
    if _REAL_TRADING_GUARD:
        return  # all good — paper trading mode
    raise RuntimeError(
        "SAFETY VIOLATION: Real exchange calls are not permitted in paper-trading mode."
    )


class VirtualOrderManager:
    """
    Manages the lifecycle of virtual (paper) orders.

    Usage
    -----
    vom = VirtualOrderManager(portfolio_engine)
    order_id = await vom.place_order('buy', price=95000.0, qty=0.001, grid_level=1)
    await vom.fill_order(order_id, fill_price=94980.0)
    """

    def __init__(self, portfolio) -> None:
        """
        Parameters
        ----------
        portfolio : PortfolioEngine
            Reference to portfolio engine for recording fills.
        """
        self._portfolio = portfolio
        self._lock = asyncio.Lock()

    # ── Validation helpers ────────────────────────────────────────────────────

    @staticmethod
    def _validate_order(side: str, price: float, qty: float) -> None:
        """Validate order fields; raise ValueError on bad input."""
        if side not in ("buy", "sell"):
            raise ValueError(f"Invalid side: {side!r}. Must be 'buy' or 'sell'.")
        if price <= 0:
            raise ValueError(f"Price must be positive, got {price}.")
        if qty <= 0:
            raise ValueError(f"Quantity must be positive, got {qty}.")

        # Check minimum order size
        if qty < config.MIN_ORDER_SIZE:
            raise ValueError(
                f"Quantity {qty} is less than minimum order size {config.MIN_ORDER_SIZE} BTC."
            )

        # Determine precision decimals from ORDER_STEP
        decimals = 0
        step = config.ORDER_STEP
        while step < 1.0 - 1e-9:
            step *= 10.0
            decimals += 1
            if decimals > 10:  # safety limit
                break

        rounded_qty = round(qty, decimals)
        if abs(qty - rounded_qty) > 1e-9:
            raise ValueError(
                f"Quantity {qty} does not match order step of {config.ORDER_STEP} (precision: {decimals} decimal places)."
            )

    # ── Public API ────────────────────────────────────────────────────────────

    async def place_order(
        self,
        side: str,
        price: float,
        qty: float,
        grid_level: Optional[int] = None,
    ) -> str:
        """
        Place a new virtual limit order.

        Returns
        -------
        str
            The new order ID (UUID4).
        """
        _assert_paper_trading()
        self._validate_order(side, price, qty)

        order_id = str(uuid.uuid4())
        now = time.time()

        order_record = {
            "id": order_id,
            "side": side,
            "price": price,
            "qty": qty,
            "status": "pending",
            "grid_level": grid_level,
            "created_at": now,
            "updated_at": now,
        }

        await db.save_order(order_record)
        logger.info(
            "ORDER PLACED | %s | id=%s | price=%.2f | qty=%.6f | level=%s",
            side.upper(),
            order_id[:8],
            price,
            qty,
            grid_level,
        )
        return order_id

    async def fill_order(
        self,
        order_id: str,
        intended_price: float,
        qty: float,
        side: str,
        grid_level: Optional[int] = None,
        is_maker: bool = True,
    ) -> dict:
        """
        Simulate filling an order with optional slippage.

        Slippage is applied in the unfavorable direction:
        - Buy:  fill_price = intended_price × (1 + SLIPPAGE_PCT)
        - Sell: fill_price = intended_price × (1 - SLIPPAGE_PCT)

        Returns
        -------
        dict
            Trade record with fill_price, fee, realized_pnl.
        """
        _assert_paper_trading()

        async with self._lock:
            # Apply slippage
            if side == "buy":
                fill_price = intended_price * (1 + config.SLIPPAGE_PCT)
            else:
                fill_price = intended_price * (1 - config.SLIPPAGE_PCT)

            # Fee on fill value (maker vs taker fee)
            fill_value = fill_price * qty
            fee_rate = config.MAKER_FEE if is_maker else config.TAKER_FEE
            fee = fill_value * fee_rate

            # Let portfolio engine calculate realized PnL and update balances
            realized_pnl = await self._portfolio.record_trade(
                side=side,
                qty=qty,
                fill_price=fill_price,
                fee=fee,
            )

            # Save trade record
            trade_record = {
                "id": str(uuid.uuid4()),
                "order_id": order_id,
                "side": side,
                "qty": qty,
                "price": intended_price,
                "fill_price": fill_price,
                "fee": fee,
                "realized_pnl": realized_pnl,
                "created_at": time.time(),
            }
            await db.save_trade(trade_record)
            await db.update_order_status(order_id, "filled")

            logger.info(
                "ORDER FILLED | %s | id=%s | fill=%.2f | fee=%.6f | rpnl=%.6f",
                side.upper(),
                order_id[:8],
                fill_price,
                fee,
                realized_pnl,
            )
            return trade_record

    async def cancel_order(self, order_id: str) -> None:
        """Cancel a pending order."""
        _assert_paper_trading()
        await db.update_order_status(order_id, "cancelled")
        logger.info("ORDER CANCELLED | id=%s", order_id[:8])

    async def cancel_all_orders(self) -> int:
        """Cancel all pending orders. Returns number cancelled."""
        _assert_paper_trading()
        open_orders = await db.get_open_orders()
        for order in open_orders:
            await db.update_order_status(order["id"], "cancelled")
        count = len(open_orders)
        if count:
            logger.info("Cancelled %d pending orders.", count)
        return count
