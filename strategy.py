"""
strategy.py
===========
DCA/Grid trading strategy engine.

Producer-Consumer: consumes price ticks from asyncio.Queue produced by market_ws.py.

Grid logic
----------
- Grid is built symmetrically around a center price.
- Buy levels are below center, sell levels above.
- When a buy fires → immediately place a sell one grid_spacing above.
- When a sell fires → immediately place a buy one grid_spacing below.
- If price drifts beyond the grid bounds by > 50% of total grid width → rebuild grid.

State
-----
Grid levels are tracked in memory as a list of active "level slots":
    {
        "order_id": str | None,
        "side": "buy" | "sell",
        "price": float,
        "level": int,        # negative=buy side, positive=sell side
        "active": bool,
    }
"""
import asyncio
import logging
import time
from typing import Optional

import config
import database as db
from virtual_orders import VirtualOrderManager
from portfolio import PortfolioEngine

logger = logging.getLogger(__name__)


class GridStrategy:
    """
    Grid/DCA strategy engine.

    Parameters
    ----------
    queue          : asyncio.Queue — price ticks from market_ws
    order_manager  : VirtualOrderManager
    portfolio      : PortfolioEngine
    """

    def __init__(
        self,
        queue: asyncio.Queue,
        order_manager: VirtualOrderManager,
        portfolio: PortfolioEngine,
    ) -> None:
        self._queue = queue
        self._om = order_manager
        self._portfolio = portfolio

        self._center_price: float = 0.0
        self._last_price: float = 0.0
        self._grid: list[dict] = []   # active grid level records
        self._running: bool = False

        # Map price → level dict for O(1) trigger lookup
        self._buy_levels: dict[float, dict] = {}
        self._sell_levels: dict[float, dict] = {}

    # ── Grid construction ─────────────────────────────────────────────────────

    def _build_grid(self, center: float) -> None:
        """
        Build symmetric grid around center price.

        Creates GRID_LEVELS buy orders below and GRID_LEVELS sell orders above.
        """
        self._center_price = center
        self._grid = []
        self._buy_levels = {}
        self._sell_levels = {}

        spacing = config.GRID_SPACING
        levels = config.GRID_LEVELS

        for i in range(1, levels + 1):
            buy_price = round(center - i * spacing, 2)
            sell_price = round(center + i * spacing, 2)

            buy_level = {
                "order_id": None,
                "side": "buy",
                "price": buy_price,
                "level": -i,
                "active": True,
                "pending": False,
            }
            sell_level = {
                "order_id": None,
                "side": "sell",
                "price": sell_price,
                "level": i,
                "active": True,
                "pending": False,
            }

            self._grid.append(buy_level)
            self._grid.append(sell_level)
            self._buy_levels[buy_price] = buy_level
            self._sell_levels[sell_price] = sell_level

        logger.info(
            "Grid built | center=%.2f | %d buy levels | %d sell levels",
            center,
            levels,
            levels,
        )
        for lvl in sorted(self._grid, key=lambda x: x["price"], reverse=True):
            logger.debug("  [%+d] %s @ %.2f", lvl["level"], lvl["side"].upper(), lvl["price"])

    async def _place_grid_orders(self) -> None:
        """Place virtual pending orders for all active grid levels."""
        # Calculate how much base asset (BTC) is currently available for new sell orders
        # (total balance minus base asset already committed to pending sell orders)
        open_orders = await db.get_open_orders()
        committed_base = sum(order["qty"] for order in open_orders if order["side"] == "sell")
        available_base = self._portfolio.balance_base - committed_base

        for lvl in self._grid:
            if not lvl["active"] or lvl["pending"] or lvl["order_id"] is not None:
                continue

            # In spot trading, we cannot place sell orders unless we own the base asset (BTC)
            if lvl["side"] == "sell":
                if available_base >= config.ORDER_SIZE:
                    available_base -= config.ORDER_SIZE
                else:
                    # Skip placing this sell order since we don't have enough BTC
                    continue

            order_id = await self._om.place_order(
                side=lvl["side"],
                price=lvl["price"],
                qty=config.ORDER_SIZE,
                grid_level=lvl["level"],
            )
            lvl["order_id"] = order_id
            lvl["pending"] = True

        # Persist center price
        await db.save_param("center_price", str(self._center_price))
        await db.save_param("grid_built_at", str(time.time()))

    # ── Grid rebuild trigger ──────────────────────────────────────────────────

    def _should_rebuild(self, price: float) -> bool:
        """
        Return True if price has drifted outside the grid by more than
        50% of the total grid width — requires a rebalance.
        """
        if not self._grid:
            return False
        total_width = config.GRID_LEVELS * config.GRID_SPACING
        lower_bound = self._center_price - config.GRID_LEVELS * config.GRID_SPACING
        upper_bound = self._center_price + config.GRID_LEVELS * config.GRID_SPACING
        margin = total_width * 0.5

        return price < (lower_bound - margin) or price > (upper_bound + margin)

    async def _rebuild_grid(self, price: float) -> None:
        """Cancel all pending orders and rebuild the grid around new price."""
        logger.warning("Price %.2f outside grid bounds — rebuilding grid.", price)
        await self._om.cancel_all_orders()
        new_center = round(price / config.GRID_SPACING) * config.GRID_SPACING
        self._build_grid(new_center)
        await self._place_grid_orders()

    # ── Trigger engine ────────────────────────────────────────────────────────

    async def _check_triggers(self, price: float) -> None:
        """
        Check all active grid levels against current price.
        Trigger any that have been crossed.
        """
        # Check buy levels (trigger when price drops to or below level)
        triggered_buys = [
            lvl for lvl in self._buy_levels.values()
            if lvl["active"] and lvl["pending"] and price <= lvl["price"]
        ]
        for lvl in triggered_buys:
            await self._execute_fill(lvl, price)

        # Check sell levels (trigger when price rises to or above level)
        triggered_sells = [
            lvl for lvl in self._sell_levels.values()
            if lvl["active"] and lvl["pending"] and price >= lvl["price"]
        ]
        for lvl in triggered_sells:
            await self._execute_fill(lvl, price)

    async def _execute_fill(self, lvl: dict, market_price: float) -> None:
        """Fill a triggered grid level and place the opposing order."""
        if not lvl["active"] or not lvl["pending"]:
            return

        order_id = lvl["order_id"]
        side = lvl["side"]
        price = lvl["price"]

        logger.info(
            "TRIGGER | %s @ %.2f (market=%.2f) | level=%d",
            side.upper(), price, market_price, lvl["level"]
        )

        # Simulate fill
        await self._om.fill_order(
            order_id=order_id,
            intended_price=price,
            qty=config.ORDER_SIZE,
            side=side,
            grid_level=lvl["level"],
        )

        # Mark level as filled (deactivate)
        lvl["active"] = False
        lvl["pending"] = False

        # Place opposing order one grid spacing away
        await self._place_opposing_order(side, price, lvl["level"])

    async def _place_opposing_order(
        self, filled_side: str, filled_price: float, filled_level: int
    ) -> None:
        """
        After a fill, place the opposite order one level away.

        Buy filled  → sell one grid_spacing above
        Sell filled → buy one grid_spacing below
        """
        if filled_side == "buy":
            new_side = "sell"
            new_price = round(filled_price + config.GRID_SPACING, 2)
            new_level = abs(filled_level)
        else:
            new_side = "buy"
            new_price = round(filled_price - config.GRID_SPACING, 2)
            new_level = -abs(filled_level)

        # Check if this price already exists in the grid
        target_map = self._sell_levels if new_side == "sell" else self._buy_levels
        if new_price in target_map and target_map[new_price]["active"]:
            logger.debug("Opposing level already active at %.2f — skipping.", new_price)
            return

        order_id = await self._om.place_order(
            side=new_side,
            price=new_price,
            qty=config.ORDER_SIZE,
            grid_level=new_level,
        )

        new_level_dict = {
            "order_id": order_id,
            "side": new_side,
            "price": new_price,
            "level": new_level,
            "active": True,
            "pending": True,
        }
        self._grid.append(new_level_dict)
        if new_side == "buy":
            self._buy_levels[new_price] = new_level_dict
        else:
            self._sell_levels[new_price] = new_level_dict

        logger.info(
            "OPPOSING ORDER | %s @ %.2f | level=%d",
            new_side.upper(), new_price, new_level
        )

    # ── Initialization ────────────────────────────────────────────────────────

    async def initialize(self, center_price: float) -> None:
        """Set up the grid around the given center price."""
        # Check for existing grid in DB
        saved_center = await db.get_param("center_price")
        if saved_center:
            try:
                center_price = float(saved_center)
                logger.info("Resuming grid from saved center: %.2f", center_price)
            except ValueError:
                pass

        # AUTOMATIC INITIAL MARKET BUY IF BASE BALANCE IS INSUFFICIENT
        required_base = config.GRID_LEVELS * config.ORDER_SIZE
        missing_base = required_base - self._portfolio.balance_base

        if missing_base > 0:
            logger.info(
                "Base balance %.6f BTC is less than required %.6f BTC for grid SELL levels.",
                self._portfolio.balance_base,
                required_base,
            )
            # Estimate cost: price * qty * (1 + slippage + taker_fee)
            estimated_price = center_price * (1 + config.SLIPPAGE_PCT)
            estimated_cost = estimated_price * missing_base * (1 + config.TAKER_FEE)

            if estimated_cost > self._portfolio.balance_quote:
                max_cost_allowed = self._portfolio.balance_quote
                max_qty = max_cost_allowed / (estimated_price * (1 + config.TAKER_FEE))
                
                # Determine precision decimals from ORDER_STEP
                decimals = 0
                step = config.ORDER_STEP
                while step < 1.0 - 1e-9:
                    step *= 10.0
                    decimals += 1
                    if decimals > 10:
                        break
                
                factor = 10 ** decimals
                max_qty = int(max_qty * factor) / factor
                
                logger.warning(
                    "Insufficient quote balance (%.2f USDT) to buy required %.6f BTC. "
                    "Adjusting initial buy quantity to %.6f BTC.",
                    self._portfolio.balance_quote,
                    missing_base,
                    max_qty,
                )
                missing_base = max_qty

            # Round missing_base to decimals
            decimals = 0
            step = config.ORDER_STEP
            while step < 1.0 - 1e-9:
                step *= 10.0
                decimals += 1
                if decimals > 10:
                    break
            missing_base = round(missing_base, decimals)

            if missing_base >= config.MIN_ORDER_SIZE:
                logger.info(
                    "Executing initial market buy of %.6f BTC at %.2f USDT.",
                    missing_base,
                    center_price,
                )
                order_id = await self._om.place_order(
                    side="buy",
                    price=center_price,
                    qty=missing_base,
                    grid_level=0,  # 0 indicates startup/initial buy
                )
                await self._om.fill_order(
                    order_id=order_id,
                    intended_price=center_price,
                    qty=missing_base,
                    side="buy",
                    grid_level=0,
                    is_maker=False,  # market orders are takers
                )
            else:
                logger.warning(
                    "Initial buy quantity %.6f BTC is less than minimum order size %.6f BTC. "
                    "Skipping initial buy.",
                    missing_base,
                    config.MIN_ORDER_SIZE,
                )

        self._build_grid(center_price)
        await self._place_grid_orders()

    # ── Main consumer loop ────────────────────────────────────────────────────

    async def run(self) -> None:
        """Consume price ticks and check grid triggers."""
        self._running = True
        logger.info("Strategy engine started.")

        while self._running:
            try:
                # Wait for next price tick (timeout to allow shutdown checks)
                tick = await asyncio.wait_for(self._queue.get(), timeout=5.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            price: float = tick["price"]
            ts: float = tick["ts"]
            self._last_price = price

            # Stale data guard
            age = time.time() - ts
            if age > config.DATA_STALE_SECONDS:
                logger.warning("Stale tick (age=%.1fs) — skipping.", age)
                self._queue.task_done()
                continue

            # Dynamic grid rebuild if price drifted too far
            if self._should_rebuild(price):
                await self._rebuild_grid(price)
            else:
                await self._check_triggers(price)

            self._queue.task_done()

        logger.info("Strategy engine stopped.")

    async def stop(self) -> None:
        self._running = False

    # ── Status query ──────────────────────────────────────────────────────────

    def get_active_levels(self) -> list[dict]:
        """Return currently active (pending) grid levels sorted by price."""
        return sorted(
            [lvl for lvl in self._grid if lvl["active"]],
            key=lambda x: x["price"],
            reverse=True,
        )

    @property
    def last_price(self) -> float:
        return self._last_price

    @property
    def center_price(self) -> float:
        return self._center_price
