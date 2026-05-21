# DCA/Grid Paper Trading Bot — Walkthrough

## What Was Built

A complete, modular paper-trading DCA/Grid bot for WhiteBIT, implemented in **9 Python files** following the Technical Specification.

## Project Structure

```
bot_dca/
├── .env                  ← Configuration (edit this)
├── .env.example          ← Template
├── requirements.txt      ← Python dependencies
├── config.py             ← Centralized config (reads .env)
├── logging_config.py     ← Rotating file + console logging
├── database.py           ← SQLite/aiosqlite, WAL mode, all CRUD
├── virtual_orders.py     ← Paper-trading order abstraction + slippage
├── portfolio.py          ← PnL engine (realized, unrealized, drawdown)
├── market_ws.py          ← WhiteBIT WebSocket → asyncio.Queue
├── strategy.py           ← Grid trigger engine + dynamic rebalance
├── main.py               ← Wires everything, graceful shutdown
└── app.py                ← Streamlit dashboard (auto-refresh 2s)
```

## Setup & Running

### 1. Create a Virtual Environment

```bash
cd bot_dca

# Create venv
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure `.env`

The `.env` file is pre-created with defaults. Key settings:
```bash
MARKET=DBTC_DUSDT             # WhiteBIT demo pair (safe — no real funds)
INITIAL_BALANCE_QUOTE=500.0   # $500 virtual USDT
INITIAL_BALANCE_BASE=0.0      # 0.0 BTC

GRID_LEVELS=5                 # 5 buy levels + 5 sell levels
GRID_SPACING=200.0            # $200 between each grid level
ORDER_SIZE=0.0005             # 0.0005 BTC per order

MIN_ORDER_SIZE=0.00001        # Minimum order size limit (0.00001 BTC)
ORDER_STEP=0.000001           # Volume precision step (6 decimal places)

MAKER_FEE=0.0007              # Maker fee of 0.07%
TAKER_FEE=0.00095             # Taker fee of 0.095%
```

### 3. Run the Bot

**Terminal 1 — Start the trading bot:**
```bash
source venv/bin/activate
python main.py
```

You'll see output like:
```
2026-05-21 19:15:00 | INFO     | __main__ | Fetched center price from DBTC_DUSDT: 95000.00
2026-05-21 19:15:00 | INFO     | strategy | Grid built | center=95000.00 | 5 buy levels | 5 sell levels
2026-05-21 19:15:01 | INFO     | market_ws | WebSocket connected.
2026-05-21 19:15:01 | INFO     | market_ws | Price update: 94823.50
```

**Terminal 2 — Start the dashboard:**
```bash
source venv/bin/activate
streamlit run app.py
```
Opens at **http://localhost:8501**

## Module Architecture

```
market_ws.py ──(price ticks)──► asyncio.Queue ──► strategy.py
                                                        │
                                              ┌_________┴_________┐
                                              ▼                   ▼
                                    virtual_orders.py       database.py
                                              │
                                              ▼
                                        portfolio.py
                                              │
                                    ┌_________┴_________┐
                                    ▼                   ▼
                                database.py          app.py (reads DB)
```

## Key Design Decisions

| Feature | Implementation |
|---------|---------------|
| **Paper-Trading Guard** | `virtual_orders.py` raises `RuntimeError` if real API call attempted |
| **WAL Mode** | All SQLite connections use `PRAGMA journal_mode=WAL` — dashboard reads never block bot writes |
| **Exponential Backoff** | WebSocket reconnects: 1s → 2s → 4s … max 60s |
| **Stale Data Guard** | Ticks older than `DATA_STALE_SECONDS` (30s) are discarded |
| **Dynamic Grid** | If price drifts >50% of grid width beyond bounds → grid rebuilds around new price |
| **Opposing Orders** | Buy fill → sell placed one spacing above; Sell fill → buy placed one spacing below |

## Grid Example (5 levels, $200 spacing, center $95,000)

```
SELL @ $96,000  ← level +1
SELL @ $96,200  ← level +2
SELL @ $96,400  ← level +3
SELL @ $96,600  ← level +4
SELL @ $96,800  ← level +5
──────────── Center: $95,000 ────────────
BUY  @ $94,800  ← level -1
BUY  @ $94,600  ← level -2
BUY  @ $94,400  ← level -3
BUY  @ $94,200  ← level -4
BUY  @ $94,000  ← level -5
```

## Dashboard Features

- **KPI Cards**: Quote balance, Base balance, Realized PnL, Total Equity
- **Equity Curve**: Plotly line chart built from cumulative trade history
- **Active Orders**: Table of pending grid orders
- **Recent Trades**: Last 20 fills with price, fee, PnL
- **Grid Visualization**: Price-level diagram showing active buy/sell lines

> [!NOTE]
> The DBTC_DUSDT pair is WhiteBIT's demo pair — prices mirror BTC/USDT but use virtual demo funds on the real exchange. Our bot is purely paper-trading locally in SQLite, independent of any exchange account.
