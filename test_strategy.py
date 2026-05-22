import unittest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import sys

# Add virtualenv packages to path
sys.path.insert(0, 'venv/lib/python3.12/site-packages')

import config
# Set config values for test
config.INITIAL_BALANCE_QUOTE = 500.0
config.INITIAL_BALANCE_BASE = 0.0
config.GRID_LEVELS = 5
config.GRID_SPACING = 400.0
config.ORDER_SIZE = 0.001
config.MIN_ORDER_SIZE = 0.00001
config.ORDER_STEP = 0.000001

from portfolio import PortfolioEngine
from virtual_orders import VirtualOrderManager
from strategy import GridStrategy

class TestStartupGridBehavior(unittest.IsolatedAsyncioTestCase):
    @patch('database.save_order', new_callable=AsyncMock)
    @patch('database.update_order_status', new_callable=AsyncMock)
    @patch('database.save_trade', new_callable=AsyncMock)
    @patch('database.update_balance', new_callable=AsyncMock)
    @patch('database.get_open_orders', new_callable=AsyncMock, return_value=[])
    @patch('database.save_param', new_callable=AsyncMock)
    @patch('database.get_param', new_callable=AsyncMock, return_value=None)
    async def test_startup_buy_only_mode_when_insufficient_base(
        self, mock_get_param, mock_save_param, mock_get_open_orders, mock_update_balance,
        mock_save_trade, mock_update_order_status, mock_save_order
    ):
        """When base balance is 0, bot should NOT execute a market buy.
        Instead it should start in buy-only mode with only BUY grid orders placed."""
        portfolio = PortfolioEngine()
        portfolio._balance_quote = 500.0
        portfolio._balance_base = 0.0

        om = VirtualOrderManager(portfolio)

        strategy = GridStrategy(
            queue=asyncio.Queue(),
            order_manager=om,
            portfolio=portfolio
        )

        center_price = 90000.0
        await strategy.initialize(center_price)

        # No initial market buy should have been executed — no taker fees on startup
        mock_save_trade.assert_not_called()

        # Only BUY-side grid orders should be placed (no sell orders since no BTC)
        placed_orders = [call.args[0] for call in mock_save_order.call_args_list]

        buy_orders = [o for o in placed_orders if o['side'] == 'buy']
        sell_orders = [o for o in placed_orders if o['side'] == 'sell']

        # All 5 buy levels should be placed
        self.assertEqual(len(buy_orders), 5)
        # No sell orders (no BTC to back them)
        self.assertEqual(len(sell_orders), 0)

        # Base balance should remain 0 (no market buy executed)
        self.assertAlmostEqual(portfolio.balance_base, 0.0)
        # Quote balance should remain unchanged (no spend on startup)
        self.assertAlmostEqual(portfolio.balance_quote, 500.0)

    @patch('database.save_order', new_callable=AsyncMock)
    @patch('database.update_order_status', new_callable=AsyncMock)
    @patch('database.save_trade', new_callable=AsyncMock)
    @patch('database.update_balance', new_callable=AsyncMock)
    @patch('database.get_open_orders', new_callable=AsyncMock, return_value=[])
    @patch('database.save_param', new_callable=AsyncMock)
    @patch('database.get_param', new_callable=AsyncMock, return_value=None)
    async def test_startup_full_grid_when_sufficient_base(
        self, mock_get_param, mock_save_param, mock_get_open_orders, mock_update_balance,
        mock_save_trade, mock_update_order_status, mock_save_order
    ):
        """When base balance is sufficient, both buy and sell grid orders should be placed."""
        portfolio = PortfolioEngine()
        portfolio._balance_quote = 500.0
        portfolio._balance_base = 0.005  # 5 * 0.001 BTC = enough for all sell levels

        om = VirtualOrderManager(portfolio)

        strategy = GridStrategy(
            queue=asyncio.Queue(),
            order_manager=om,
            portfolio=portfolio
        )

        center_price = 90000.0
        await strategy.initialize(center_price)

        # No initial market buy (base is already sufficient)
        mock_save_trade.assert_not_called()

        placed_orders = [call.args[0] for call in mock_save_order.call_args_list]
        buy_orders = [o for o in placed_orders if o['side'] == 'buy']
        sell_orders = [o for o in placed_orders if o['side'] == 'sell']

        self.assertEqual(len(buy_orders), 5)
        self.assertEqual(len(sell_orders), 5)

if __name__ == '__main__':
    unittest.main()
