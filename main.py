"""
main.py
=======
Entry point for the DCA/Grid paper-trading bot.

Wires together all modules:
    market_ws (Producer) → asyncio.Queue → strategy (Consumer)

Handles graceful shutdown on SIGINT / SIGTERM.

Usage
-----
    python main.py
"""
import argparse
import asyncio
import logging
import os
from pathlib import Path
import signal
import sys

import requests

import config
import database as db
import logging_config
from market_ws import MarketDataProducer
from portfolio import PortfolioEngine
from virtual_orders import VirtualOrderManager
from strategy import GridStrategy

logging_config.setup_logging()
logger = logging.getLogger(__name__)


def fetch_center_price() -> float:
    """
    Fetch current market price via public REST API.
    Used to initialize the grid center on startup.
    """
    try:
        url = f"{config.BASE_URL}/api/v4/public/ticker"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        # Try DBTC_DUSDT first (demo pair), fall back to BTC_USDT
        for market in (config.MARKET, "BTC_USDT"):
            if market in data:
                price = float(data[market]["last_price"])
                logger.info(
                    "Fetched center price from %s: %.2f", market, price
                )
                return price

        raise ValueError(f"Market {config.MARKET} not found in ticker response.")

    except Exception as exc:
        logger.error("Failed to fetch ticker price: %s", exc)
        # Fallback: use a reasonable default (user can adjust via .env)
        fallback = 95000.0
        logger.warning("Using fallback center price: %.2f", fallback)
        return fallback


async def main(reset: bool = False) -> None:
    """Main async entry point."""
    logger.info("=" * 60)
    logger.info("DCA/Grid Paper Trading Bot starting...")
    logger.info("Market: %s | Levels: %d | Spacing: %.2f | Size: %.6f",
                config.MARKET, config.GRID_LEVELS, config.GRID_SPACING, config.ORDER_SIZE)
    logger.info("=" * 60)

    # 0. Single instance enforcement using PID file
    pid_file = Path(__file__).parent / "bot_dca.pid"
    my_pid = os.getpid()
    if pid_file.exists():
        try:
            with open(pid_file, "r") as f:
                old_pid = int(f.read().strip())
            if old_pid != my_pid:
                # Check if old_pid is running
                try:
                    os.kill(old_pid, 0)
                    logger.info("Found existing bot instance with PID %d. Terminating it...", old_pid)
                    os.kill(old_pid, signal.SIGTERM)
                    # Wait for up to 3 seconds for it to exit
                    for _ in range(6):
                        await asyncio.sleep(0.5)
                        try:
                            os.kill(old_pid, 0)
                        except OSError:
                            break
                    else:
                        logger.warning("Old instance PID %d did not terminate. Force killing...", old_pid)
                        os.kill(old_pid, signal.SIGKILL)
                except OSError:
                    # Not running
                    pass
        except Exception as e:
            logger.error("Error reading or handling PID file: %s", e)

    # Write current PID
    try:
        with open(pid_file, "w") as f:
            f.write(str(my_pid))
    except Exception as e:
        logger.error("Failed to write PID file: %s", e)

    try:
        # Validate order parameters on startup
        if config.ORDER_SIZE < config.MIN_ORDER_SIZE:
            logger.critical(
                "Configuration error: ORDER_SIZE (%.6f) is less than MIN_ORDER_SIZE (%.6f) BTC.",
                config.ORDER_SIZE,
                config.MIN_ORDER_SIZE,
            )
            sys.exit(1)

        # Determine precision decimals from ORDER_STEP
        decimals = 0
        step = config.ORDER_STEP
        while step < 1.0 - 1e-9:
            step *= 10.0
            decimals += 1
            if decimals > 10:
                break

        rounded_size = round(config.ORDER_SIZE, decimals)
        if abs(config.ORDER_SIZE - rounded_size) > 1e-9:
            logger.critical(
                "Configuration error: ORDER_SIZE (%.6f) does not match ORDER_STEP of %f (precision: %d decimal places).",
                config.ORDER_SIZE,
                config.ORDER_STEP,
                decimals,
            )
            sys.exit(1)

        # 1. Initialize database
        await db.init_db()
        if reset:
            await db.reset_db()
            logger.info("Database reset requested via --reset flag. All history cleared.")

        # 2. Initialize portfolio engine (restores from DB)
        portfolio = PortfolioEngine()
        await portfolio.load_from_db()

        # 3. Set up shared price queue (max 100 items — prevents memory bloat)
        price_queue: asyncio.Queue = asyncio.Queue(maxsize=100)

        # 4. Create order manager
        order_manager = VirtualOrderManager(portfolio)

        # 5. Cancel any pending orders left from a previous run (clean slate)
        cancelled = await order_manager.cancel_all_orders()
        if cancelled:
            logger.info("Cleaned up %d stale orders from previous run.", cancelled)

        # 6. Fetch current price for grid center
        center_price = fetch_center_price()

        # 7. Initialize strategy
        strategy = GridStrategy(price_queue, order_manager, portfolio)
        await strategy.initialize(center_price)

        # 8. Create WebSocket producer
        ws_producer = MarketDataProducer(price_queue)

        # ── Shutdown handling ──────────────────────────────────────────────────────
        loop = asyncio.get_running_loop()
        shutdown_event = asyncio.Event()

        def _shutdown_signal_handler() -> None:
            logger.info("Shutdown signal received.")
            shutdown_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _shutdown_signal_handler)
            except NotImplementedError:
                # Windows doesn't support add_signal_handler
                pass

        # ── Launch tasks ────────────────────────────────────────────────────────────
        tasks = [
            asyncio.create_task(ws_producer.run(), name="market_ws"),
            asyncio.create_task(strategy.run(), name="strategy"),
        ]

        logger.info("Bot running. Press Ctrl+C to stop.")

        try:
            # Wait until shutdown is signaled
            await shutdown_event.wait()
        except asyncio.CancelledError:
            pass

        # ── Graceful shutdown ───────────────────────────────────────────────────────
        logger.info("Shutting down...")
        await ws_producer.stop()
        await strategy.stop()

        for task in tasks:
            task.cancel()

        await asyncio.gather(*tasks, return_exceptions=True)

        metrics = portfolio.get_metrics(strategy.last_price)
        logger.info("Final portfolio metrics:")
        for k, v in metrics.items():
            logger.info("  %-25s %s", k + ":", v)

        logger.info("Bot stopped cleanly.")
    finally:
        # Remove PID file if we are the owner
        if pid_file.exists():
            try:
                with open(pid_file, "r") as f:
                    file_pid = int(f.read().strip())
                if file_pid == my_pid:
                    pid_file.unlink()
            except Exception:
                pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DCA/Grid Paper Trading Bot")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Clear all trade history and reset balances to initial values before starting.",
    )
    args = parser.parse_args()
    try:
        asyncio.run(main(reset=args.reset))
    except KeyboardInterrupt:
        sys.exit(0)
