"""
portfolio.py
============
Position tracking and PnL calculation engine.

Formulas
--------
Weighted average cost:
    P_avg = Σ(price_i × qty_i) / Σ(qty_i)

Realized PnL (on sell):
    realized_pnl = (fill_price - avg_cost) × qty - fee

Unrealized PnL:
    unrealized_pnl = (last_price - avg_cost) × position_qty

Max Drawdown:
    Tracks running peak equity and max dip below it.
"""
import asyncio
import logging

import database as db
import config

logger = logging.getLogger(__name__)


class PortfolioEngine:
    """
    Maintains in-memory state for positions, balances and PnL.
    All state is also persisted to the database after each trade.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()

        # Balances (loaded from DB on startup)
        self._balance_quote: float = config.INITIAL_BALANCE_QUOTE
        self._balance_base: float = config.INITIAL_BALANCE_BASE

        # Position tracking
        self._position_qty: float = 0.0   # open BASE position
        self._avg_cost: float = 0.0        # weighted average buy price

        # PnL accumulators
        self._realized_pnl: float = 0.0
        self._total_fees: float = 0.0

        # Drawdown tracking
        self._peak_equity: float = 0.0
        self._max_drawdown: float = 0.0

        # Trade counters (for win/loss ratio)
        self._winning_trades: int = 0
        self._losing_trades: int = 0

    # ── Initialization ────────────────────────────────────────────────────────

    async def load_from_db(self) -> None:
        """Restore balances and position stats from persisted state.

        IMPORTANT: Balances (quote/base) are loaded directly from the DB snapshot
        because they were already updated after every trade.
        Trade history is replayed ONLY to reconstruct position tracking stats
        (avg_cost, position_qty, realized_pnl, etc.) without touching balances.
        This avoids the double-application bug.
        """
        # Step 1: Load the authoritative balance snapshot from DB
        balances = await db.get_all_balances()
        restored_quote = balances.get("QUOTE", config.INITIAL_BALANCE_QUOTE)
        restored_base = balances.get("BASE", config.INITIAL_BALANCE_BASE)

        # Step 2: Replay trades to reconstruct position stats ONLY (no balance changes)
        trades = await db.get_all_trades()
        for trade in trades:
            self._replay_trade_stats(
                side=trade["side"],
                qty=trade["qty"],
                fill_price=trade["fill_price"],
                fee=trade["fee"],
            )

        # Step 3: Restore the authoritative balances from DB (overwrite any accidental changes)
        self._balance_quote = restored_quote
        self._balance_base = restored_base

        logger.info(
            "Portfolio restored | quote=%.2f | base=%.6f | avg_cost=%.2f | rpnl=%.4f",
            self._balance_quote,
            self._balance_base,
            self._avg_cost,
            self._realized_pnl,
        )

    def _replay_trade_stats(
        self,
        side: str,
        qty: float,
        fill_price: float,
        fee: float,
    ) -> None:
        """Replay a historical trade to reconstruct position tracking stats.

        This method updates ONLY:
            _position_qty, _avg_cost, _realized_pnl, _total_fees,
            _winning_trades, _losing_trades

        It does NOT modify _balance_quote or _balance_base — those come
        directly from the DB snapshot to avoid double-application.
        """
        self._total_fees += fee

        if side == "buy":
            total_cost = self._avg_cost * self._position_qty + fill_price * qty + fee
            self._position_qty += qty
            self._avg_cost = total_cost / self._position_qty if self._position_qty else 0.0
        else:  # sell
            covered_qty = min(qty, self._position_qty)
            realized_pnl = (fill_price - self._avg_cost) * covered_qty - fee
            self._realized_pnl += realized_pnl

            if realized_pnl >= 0:
                self._winning_trades += 1
            else:
                self._losing_trades += 1

            self._position_qty = max(0.0, self._position_qty - qty)
            if self._position_qty == 0:
                self._avg_cost = 0.0

    # ── Core trade recording ─────────────────────────────────────────────────

    async def record_trade(
        self,
        side: str,
        qty: float,
        fill_price: float,
        fee: float,
    ) -> float:
        """
        Apply a completed trade to the portfolio.

        Returns
        -------
        float
            Realized PnL for this trade (0 for buys).
        """
        async with self._lock:
            return await self._apply_trade(side, qty, fill_price, fee, persist=True)

    async def _apply_trade(
        self,
        side: str,
        qty: float,
        fill_price: float,
        fee: float,
        persist: bool = True,
    ) -> float:
        """Internal: apply trade to state, optionally persist balances."""
        self._total_fees += fee
        realized_pnl = 0.0

        if side == "buy":
            cost = fill_price * qty
            if cost + fee > self._balance_quote:
                logger.warning(
                    "Insufficient quote balance (%.2f) for buy cost %.2f + fee %.4f",
                    self._balance_quote,
                    cost,
                    fee,
                )
                # Clamp — paper trading, so we allow it but log warning
            self._balance_quote -= cost + fee
            # Update weighted average cost (include fee in the cost basis)
            total_cost = self._avg_cost * self._position_qty + fill_price * qty + fee
            self._position_qty += qty
            self._avg_cost = total_cost / self._position_qty if self._position_qty else 0.0
            self._balance_base += qty

        else:  # sell
            if qty > self._balance_base:
                logger.warning(
                    "Insufficient base balance (%.6f) for sell qty %.6f",
                    self._balance_base,
                    qty,
                )
            revenue = fill_price * qty
            self._balance_quote += revenue - fee
            self._balance_base -= qty

            # Realized PnL
            covered_qty = min(qty, self._position_qty)
            realized_pnl = (fill_price - self._avg_cost) * covered_qty - fee
            self._realized_pnl += realized_pnl

            if realized_pnl >= 0:
                self._winning_trades += 1
            else:
                self._losing_trades += 1

            # Reduce position
            self._position_qty = max(0.0, self._position_qty - qty)
            if self._position_qty == 0:
                self._avg_cost = 0.0

        if persist:
            await db.update_balance("QUOTE", self._balance_quote)
            await db.update_balance("BASE", self._balance_base)

        return realized_pnl

    # ── PnL queries ───────────────────────────────────────────────────────────

    def unrealized_pnl(self, last_price: float) -> float:
        """Calculate floating PnL for the current open position."""
        if self._position_qty <= 0 or self._avg_cost <= 0:
            return 0.0
        return (last_price - self._avg_cost) * self._position_qty

    def total_equity(self, last_price: float) -> float:
        """Total portfolio value in quote currency."""
        return self._balance_quote + self._balance_base * last_price

    def _update_drawdown(self, equity: float) -> None:
        """Update peak equity and max drawdown."""
        if equity > self._peak_equity:
            self._peak_equity = equity
        if self._peak_equity > 0:
            drawdown = (self._peak_equity - equity) / self._peak_equity
            if drawdown > self._max_drawdown:
                self._max_drawdown = drawdown

    def get_metrics(self, last_price: float = 0.0) -> dict:
        """Return a snapshot of all portfolio metrics."""
        equity = self.total_equity(last_price)
        self._update_drawdown(equity)

        total_trades = self._winning_trades + self._losing_trades
        win_rate = (
            self._winning_trades / total_trades if total_trades > 0 else 0.0
        )

        return {
            "balance_quote": self._balance_quote,
            "balance_base": self._balance_base,
            "position_qty": self._position_qty,
            "avg_cost": self._avg_cost,
            "realized_pnl": self._realized_pnl,
            "unrealized_pnl": self.unrealized_pnl(last_price),
            "total_equity": equity,
            "total_fees": self._total_fees,
            "max_drawdown_pct": self._max_drawdown * 100,
            "win_rate_pct": win_rate * 100,
            "winning_trades": self._winning_trades,
            "losing_trades": self._losing_trades,
            "total_trades": total_trades,
        }

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def balance_quote(self) -> float:
        return self._balance_quote

    @property
    def balance_base(self) -> float:
        return self._balance_base

    @property
    def position_qty(self) -> float:
        return self._position_qty

    @property
    def avg_cost(self) -> float:
        return self._avg_cost

    @property
    def realized_pnl(self) -> float:
        return self._realized_pnl
