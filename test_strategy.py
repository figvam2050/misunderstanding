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
config.GRID_SPACING = 200.0
config.ORDER_SIZE = 0.0005
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
    async def test_startup_market_buy_when_insufficient_base(
        self, mock_get_param, mock_save_param, mock_get_open_orders, mock_update_balance,
        mock_save_trade, mock_update_order_status, mock_save_order
    ):
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

        # Expected missing base = 5 * 0.0005 = 0.0025 BTC
        # So portfolio base balance should now be 0.0025 BTC
        self.assertAlmostEqual(portfolio.balance_base, 0.0025)
        
        # Check that a buy order was placed at center price
        mock_save_order.assert_called()
        placed_orders = [call.args[0] for call in mock_save_order.call_args_list]
        
        # The first order placed should be the initial market buy with level=0 and qty=0.0025
        market_buy_order = placed_orders[0]
        self.assertEqual(market_buy_order['side'], 'buy')
        self.assertEqual(market_buy_order['price'], 90000.0)
        self.assertAlmostEqual(market_buy_order['qty'], 0.0025)
        self.assertEqual(market_buy_order['grid_level'], 0)

        # Check that it was filled
        mock_save_trade.assert_called()
        trade = mock_save_trade.call_args_list[0].args[0]
        self.assertEqual(trade['side'], 'buy')
        self.assertEqual(trade['price'], 90000.0)
        self.assertAlmostEqual(trade['qty'], 0.0025)

        # Check that subsequent grid orders were placed (5 buys + 5 sells)
        # Total orders placed = 1 initial market buy + 5 buys + 5 sells = 11 orders
        self.assertEqual(len(placed_orders), 11)

        # Ensure that sell orders were actually placed since we bought BTC
        sell_orders = [o for o in placed_orders[1:] if o['side'] == 'sell']
        buy_orders = [o for o in placed_orders[1:] if o['side'] == 'buy']
        self.assertEqual(len(sell_orders), 5)
        self.assertEqual(len(buy_orders), 5)

if __name__ == '__main__':
    unittest.main()
