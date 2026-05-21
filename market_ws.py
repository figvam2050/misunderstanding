"""
market_ws.py
============
Real-time market data module using WhiteBIT public WebSocket.

Subscribes to lastprice_update for the configured MARKET and pushes
price events into an asyncio.Queue for consumption by the strategy engine.

Reconnection uses exponential backoff (1s → 2s → 4s … max 60s).
Stale data (timestamp older than DATA_STALE_SECONDS) is discarded.
"""
import asyncio
import json
import logging
import time
from typing import Optional

import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

import config

logger = logging.getLogger(__name__)


class MarketDataProducer:
    """
    Connects to the WhiteBIT public WebSocket and streams last-price updates.

    The latest price is available via `.last_price` property (thread-safe read).
    New ticks are also pushed into the provided asyncio.Queue as:
        {"price": float, "ts": float}
    """

    def __init__(self, queue: asyncio.Queue) -> None:
        self._queue = queue
        self._last_price: Optional[float] = None
        self._last_ts: float = 0.0
        self._running: bool = False

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def last_price(self) -> Optional[float]:
        return self._last_price

    @property
    def last_ts(self) -> float:
        return self._last_ts

    def is_data_fresh(self) -> bool:
        """Return True if last price is within the freshness threshold."""
        if self._last_price is None:
            return False
        return (time.time() - self._last_ts) <= config.DATA_STALE_SECONDS

    # ── WebSocket connection ──────────────────────────────────────────────────

    async def run(self) -> None:
        """Main loop — connects and reconnects with exponential backoff."""
        self._running = True
        wait = config.WS_RECONNECT_MIN_WAIT

        while self._running:
            try:
                await self._connect_and_stream()
                # If we reach here cleanly, reset backoff
                wait = config.WS_RECONNECT_MIN_WAIT

            except (ConnectionClosed, WebSocketException) as exc:
                logger.warning("WebSocket disconnected: %s", exc)
            except asyncio.CancelledError:
                logger.info("MarketDataProducer cancelled.")
                break
            except Exception as exc:
                logger.error("Unexpected WebSocket error: %s", exc, exc_info=True)

            if self._running:
                logger.info("Reconnecting in %.1fs...", wait)
                await asyncio.sleep(wait)
                wait = min(wait * 2, config.WS_RECONNECT_MAX_WAIT)

        logger.info("MarketDataProducer stopped.")

    async def stop(self) -> None:
        self._running = False

    async def _connect_and_stream(self) -> None:
        """Establish WebSocket connection and process messages until disconnect."""
        logger.info("Connecting to %s", config.WS_URL)

        # Disable library-level ping — WhiteBIT uses its own protocol-level ping/pong.
        # We send a manual "ping" request every 25 seconds instead.
        async with websockets.connect(
            config.WS_URL,
            ping_interval=None,   # disable websockets library pings
            close_timeout=5,
            open_timeout=10,
        ) as ws:
            logger.info("WebSocket connected.")

            # Subscribe to last price updates (public channel, no auth needed)
            await ws.send(json.dumps({
                "id": 1,
                "method": "lastprice_subscribe",
                "params": [config.MARKET],
            }))

            # Also subscribe to deals (public trades stream — also provides price)
            await ws.send(json.dumps({
                "id": 2,
                "method": "deals_subscribe",
                "params": [[config.MARKET]],
            }))

            logger.info("Subscribed to lastprice + deals for %s", config.MARKET)

            # Heartbeat task — send server-level ping every 25 seconds
            heartbeat_task = asyncio.create_task(self._heartbeat(ws))

            try:
                async for raw_message in ws:
                    if not self._running:
                        break
                    await self._handle_message(raw_message)
            finally:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass

    async def _heartbeat(self, ws) -> None:
        """Send a ping to WhiteBIT every 25 seconds to keep connection alive."""
        ping_id = 100
        while True:
            await asyncio.sleep(25)
            try:
                await ws.send(json.dumps({
                    "id": ping_id,
                    "method": "ping",
                    "params": [],
                }))
                ping_id += 1
                logger.debug("Heartbeat ping sent (id=%d)", ping_id)
            except Exception:
                break  # ws closed — let outer loop reconnect

    async def _handle_message(self, raw: str) -> None:
        """Parse incoming WebSocket message and extract price data."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Failed to parse message: %s", raw[:200])
            return

        method = data.get("method", "")
        msg_id = data.get("id")

        # ── lastprice_update ───────────────────────────────────────────────────
        if method == "lastprice_update":
            # params: [market_name, price_string]
            params = data.get("params", [])
            if len(params) >= 2:
                try:
                    price = float(params[1])
                    await self._emit_price(price)
                except (ValueError, IndexError) as exc:
                    logger.warning("Failed to parse lastprice: %s | %s", params, exc)

        # ── deals_update — extract price from most recent deal ─────────────────
        elif method == "deals_update":
            # params: [market, [[id, ts, price, amount, side], ...]]
            params = data.get("params", [])
            if len(params) >= 2:
                deals = params[1]
                if isinstance(deals, list) and deals:
                    try:
                        # Most recent deal is first in the list
                        price = float(deals[0][2])
                        await self._emit_price(price)
                        logger.debug("Price from deal: %.2f", price)
                    except (IndexError, ValueError, TypeError):
                        pass

        # ── Subscription / ping acknowledgements ──────────────────────────────
        elif msg_id is not None and "result" in data:
            result = data.get("result")
            if result:
                logger.info("WS ack id=%s: %s", msg_id, result)
            else:
                logger.debug("WS ack id=%s: (empty)", msg_id)

        # ── Unknown message — log for diagnostics ─────────────────────────────
        else:
            logger.debug("WS msg: %s", str(data)[:120])

    async def _emit_price(self, price: float) -> None:
        """Validate and push a price tick into the queue."""
        if price <= 0:
            return
        ts = time.time()
        self._last_price = price
        self._last_ts = ts

        tick = {"price": price, "ts": ts}
        try:
            self._queue.put_nowait(tick)
        except asyncio.QueueFull:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self._queue.put_nowait(tick)

        logger.info("💰 Price: %.2f", price)
