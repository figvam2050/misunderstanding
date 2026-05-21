"""
app.py
======
Streamlit dashboard for the DCA/Grid Paper Trading Bot.

Автоматично запускає main.py при старті дашборду.
Читає дані з paper_trading.db кожні ~2 секунди.

Usage
-----
    streamlit run app.py --server.headless true
"""
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import plotly.graph_objects as go
import streamlit as st

import config
import database as db

# ── Auto-launch bot (main.py) ─────────────────────────────────────────────────
# Runs once per Streamlit server session (not on every st.rerun())
if "bot_process" not in st.session_state:
    bot_script = Path(__file__).parent / "main.py"
    try:
        proc = subprocess.Popen(
            [sys.executable, str(bot_script)],
            stdout=subprocess.DEVNULL,   # logs go to bot_dca.log
            stderr=subprocess.DEVNULL,
        )
        st.session_state["bot_process"] = proc
        st.session_state["bot_pid"] = proc.pid
        st.session_state["bot_started_at"] = datetime.now().strftime("%H:%M:%S")
    except Exception as e:
        st.session_state["bot_process"] = None
        st.session_state["bot_error"] = str(e)

# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="DCA Bot Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Custom CSS ───────────────────────────────────────────────────────────────
st.markdown(
    """
<style>
    /* Import Google Fonts */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

    /* Global reset */
    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
    }

    /* Dark theme overrides */
    .stApp {
        background: #0a0a0f;
        color: #e2e8f0;
    }

    /* Header strip */
    .dashboard-header {
        background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
        border-radius: 16px;
        padding: 20px 28px;
        margin-bottom: 20px;
        border: 1px solid rgba(255,255,255,0.08);
    }
    .dashboard-header h1 {
        font-size: 1.6rem;
        font-weight: 700;
        color: #fff;
        margin: 0;
        letter-spacing: -0.5px;
    }
    .dashboard-header .subtitle {
        color: #94a3b8;
        font-size: 0.85rem;
        margin-top: 4px;
    }

    /* KPI metric cards */
    .metric-card {
        background: linear-gradient(145deg, #1e1e2e, #16162a);
        border-radius: 14px;
        padding: 18px 20px;
        border: 1px solid rgba(255,255,255,0.07);
        transition: transform 0.2s, box-shadow 0.2s;
    }
    .metric-card:hover {
        transform: translateY(-2px);
        box-shadow: 0 8px 25px rgba(0,0,0,0.4);
    }
    .metric-label {
        color: #64748b;
        font-size: 0.72rem;
        font-weight: 500;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        margin-bottom: 6px;
    }
    .metric-value {
        font-size: 1.45rem;
        font-weight: 700;
        color: #f1f5f9;
        line-height: 1;
    }
    .metric-value.positive { color: #34d399; }
    .metric-value.negative { color: #f87171; }
    .metric-value.neutral  { color: #60a5fa; }

    /* Section headers */
    .section-header {
        font-size: 0.9rem;
        font-weight: 600;
        color: #94a3b8;
        text-transform: uppercase;
        letter-spacing: 0.1em;
        margin: 24px 0 12px;
        display: flex;
        align-items: center;
        gap: 8px;
    }
    .section-header::after {
        content: '';
        flex: 1;
        height: 1px;
        background: rgba(255,255,255,0.06);
    }

    /* Status badges */
    .badge-pending  { background:#1d4ed8; color:#bfdbfe; padding:2px 8px; border-radius:6px; font-size:0.72rem; font-weight:600; }
    .badge-filled   { background:#065f46; color:#6ee7b7; padding:2px 8px; border-radius:6px; font-size:0.72rem; font-weight:600; }
    .badge-cancelled{ background:#374151; color:#9ca3af; padding:2px 8px; border-radius:6px; font-size:0.72rem; font-weight:600; }
    .badge-buy  { background:#064e3b; color:#34d399; padding:2px 8px; border-radius:6px; font-size:0.72rem; font-weight:600; }
    .badge-sell { background:#4c1d1d; color:#f87171; padding:2px 8px; border-radius:6px; font-size:0.72rem; font-weight:600; }

    /* Streamlit overrides */
    [data-testid="stDataFrame"] { border-radius: 12px; overflow: hidden; }
    div[data-testid="metric-container"] { background: transparent; }
    .stPlotlyChart { border-radius: 14px; overflow: hidden; }

    /* Hide Streamlit branding */
    #MainMenu, footer, header { visibility: hidden; }
    .viewerBadge_container__1QSob { display: none; }
</style>
""",
    unsafe_allow_html=True,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt_price(v: float) -> str:
    return f"${v:,.2f}"


def _fmt_pnl(v: float) -> str:
    sign = "+" if v >= 0 else ""
    return f"{sign}${v:,.4f}"


def _pnl_class(v: float) -> str:
    return "positive" if v > 0 else ("negative" if v < 0 else "neutral")


def _ts_to_str(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def _metric_card(label: str, value: str, css_class: str = "") -> str:
    return f"""
    <div class="metric-card">
        <div class="metric-label">{label}</div>
        <div class="metric-value {css_class}">{value}</div>
    </div>
    """


def _section(title: str, icon: str = "") -> None:
    st.markdown(
        f'<div class="section-header">{icon} {title}</div>',
        unsafe_allow_html=True,
    )


# ── Data loaders (sync wrappers using asyncio.run) ────────────────────────────

import asyncio


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result()
        return loop.run_until_complete(coro)
    except Exception:
        return asyncio.run(coro)


@st.cache_data(ttl=2)
def load_trades():
    return _run(db.get_all_trades())


@st.cache_data(ttl=2)
def load_open_orders():
    return _run(db.get_open_orders())


@st.cache_data(ttl=2)
def load_balances():
    return _run(db.get_all_balances())


@st.cache_data(ttl=2)
def load_recent_trades(limit=50):
    return _run(db.get_trades(limit=limit))


@st.cache_data(ttl=2)
def load_recent_orders(limit=100):
    return _run(db.get_all_orders(limit=limit))


@st.cache_data(ttl=5)
def load_center_price():
    val = _run(db.get_param("center_price"))
    try:
        return float(val) if val else 0.0
    except ValueError:
        return 0.0


# ── Equity curve computation ──────────────────────────────────────────────────

def compute_equity_curve(trades: list[dict], initial_quote: float) -> tuple[list, list]:
    """Build (timestamps, equity_values) from trade history."""
    if not trades:
        return [], []

    times = []
    equities = []
    cumulative_pnl = 0.0

    for t in trades:
        cumulative_pnl += t.get("realized_pnl", 0.0) - t.get("fee", 0.0)
        times.append(datetime.fromtimestamp(t["created_at"]))
        equities.append(initial_quote + cumulative_pnl)

    return times, equities


def compute_max_drawdown(equities: list[float]) -> float:
    if not equities:
        return 0.0
    peak = equities[0]
    max_dd = 0.0
    for e in equities:
        if e > peak:
            peak = e
        dd = (peak - e) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    return max_dd * 100


def compute_win_rate(trades: list[dict]) -> tuple[float, int, int]:
    sell_trades = [t for t in trades if t["side"] == "sell"]
    if not sell_trades:
        return 0.0, 0, 0
    wins = sum(1 for t in sell_trades if t.get("realized_pnl", 0) > 0)
    losses = len(sell_trades) - wins
    return (wins / len(sell_trades)) * 100, wins, losses


# ── Main dashboard ────────────────────────────────────────────────────────────

def main():
    # ── Header ────────────────────────────────────────────────────────────────
    col_title, col_status = st.columns([4, 1])
    with col_title:
        st.markdown(
            f"""
            <div class="dashboard-header">
                <h1>📈 DCA Grid Bot Dashboard</h1>
                <div class="subtitle">
                    Paper Trading · Market: <b>{config.MARKET}</b> ·
                    Grid: {config.GRID_LEVELS} levels × ${config.GRID_SPACING:,.0f} spacing ·
                    Order size: {config.ORDER_SIZE} BTC
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with col_status:
        st.markdown("<br>", unsafe_allow_html=True)
        # Bot process status
        proc = st.session_state.get("bot_process")
        bot_running = proc is not None and proc.poll() is None
        started_at = st.session_state.get("bot_started_at", "—")
        pid = st.session_state.get("bot_pid", "—")
        bot_error = st.session_state.get("bot_error", "")

        if bot_running:
            status_html = (
                f"<div style='text-align:right;font-size:0.78rem'>"
                f"<span style='color:#34d399;font-size:1rem'>●</span> "
                f"<b style='color:#34d399'>Бот працює</b><br>"
                f"<span style='color:#64748b'>PID {pid} · з {started_at}</span>"
                f"</div>"
            )
        elif bot_error:
            status_html = (
                f"<div style='text-align:right;font-size:0.78rem'>"
                f"<span style='color:#f87171;font-size:1rem'>●</span> "
                f"<b style='color:#f87171'>Помилка запуску</b><br>"
                f"<span style='color:#64748b'>{bot_error[:40]}</span>"
                f"</div>"
            )
        else:
            status_html = (
                f"<div style='text-align:right;font-size:0.78rem'>"
                f"<span style='color:#f87171;font-size:1rem'>●</span> "
                f"<b style='color:#f87171'>Бот зупинено</b><br>"
                f"<span style='color:#64748b'>Перезапусти app.py</span>"
                f"</div>"
            )
        st.markdown(status_html, unsafe_allow_html=True)
        st.markdown(
            f"<div style='color:#64748b;font-size:0.72rem;text-align:right;margin-top:6px'>"
            f"🕐 {datetime.now().strftime('%H:%M:%S')}</div>",
            unsafe_allow_html=True,
        )


    # ── Load data ─────────────────────────────────────────────────────────────
    all_trades = load_trades()
    open_orders = load_open_orders()
    balances = load_balances()
    recent_trades = load_recent_trades()
    center_price = load_center_price()

    quote_balance = balances.get("QUOTE", config.INITIAL_BALANCE_QUOTE)
    base_balance = balances.get("BASE", 0.0)

    times, equities = compute_equity_curve(all_trades, config.INITIAL_BALANCE_QUOTE)
    max_dd = compute_max_drawdown(equities)
    win_rate, wins, losses = compute_win_rate(all_trades)

    total_realized = sum(t.get("realized_pnl", 0.0) for t in all_trades)
    total_fees = sum(t.get("fee", 0.0) for t in all_trades)
    current_equity = equities[-1] if equities else config.INITIAL_BALANCE_QUOTE

    # ── KPI Row 1 ─────────────────────────────────────────────────────────────
    _section("Portfolio Overview", "💼")
    cols = st.columns(5)

    kpis = [
        ("Grid Center", _fmt_price(center_price), "neutral"),
        ("Quote Balance", _fmt_price(quote_balance), "neutral"),
        ("Base Balance", f"{base_balance:.6f} BTC", "neutral"),
        ("Realized PnL", _fmt_pnl(total_realized), _pnl_class(total_realized)),
        ("Total Equity", _fmt_price(current_equity), _pnl_class(current_equity - config.INITIAL_BALANCE_QUOTE)),
    ]
    for col, (label, value, css) in zip(cols, kpis):
        with col:
            st.markdown(_metric_card(label, value, css), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── KPI Row 2 ─────────────────────────────────────────────────────────────
    cols2 = st.columns(5)
    kpis2 = [
        ("Total Trades", str(len(all_trades)), "neutral"),
        ("Win Rate", f"{win_rate:.1f}%", _pnl_class(win_rate - 50)),
        ("Wins / Losses", f"{wins} / {losses}", "neutral"),
        ("Max Drawdown", f"{max_dd:.2f}%", "negative" if max_dd > 5 else "positive"),
        ("Total Fees Paid", _fmt_pnl(-total_fees), "negative"),
    ]
    for col, (label, value, css) in zip(cols2, kpis2):
        with col:
            st.markdown(_metric_card(label, value, css), unsafe_allow_html=True)

    # ── Equity Curve ──────────────────────────────────────────────────────────
    _section("Equity Curve", "📉")

    if times and equities:
        fig = go.Figure()

        # Fill area under curve
        fig.add_trace(go.Scatter(
            x=times,
            y=equities,
            mode="lines",
            name="Equity",
            line=dict(color="#60a5fa", width=2.5),
            fill="tozeroy",
            fillcolor="rgba(96,165,250,0.08)",
            hovertemplate="<b>%{y:$,.2f}</b><br>%{x}<extra></extra>",
        ))

        # Zero/baseline reference
        fig.add_hline(
            y=config.INITIAL_BALANCE_QUOTE,
            line=dict(color="rgba(255,255,255,0.15)", width=1, dash="dash"),
        )

        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(14,14,24,0.6)",
            height=320,
            margin=dict(l=10, r=10, t=10, b=10),
            xaxis=dict(
                showgrid=True,
                gridcolor="rgba(255,255,255,0.04)",
                tickfont=dict(size=11, color="#64748b"),
                zeroline=False,
            ),
            yaxis=dict(
                showgrid=True,
                gridcolor="rgba(255,255,255,0.04)",
                tickprefix="$",
                tickfont=dict(size=11, color="#64748b"),
                zeroline=False,
            ),
            hoverlabel=dict(
                bgcolor="#1e293b",
                bordercolor="#334155",
                font=dict(color="#f1f5f9"),
            ),
            legend=dict(
                bgcolor="rgba(0,0,0,0)",
                bordercolor="rgba(0,0,0,0)",
                font=dict(color="#94a3b8"),
            ),
        )
        st.plotly_chart(fig, width='stretch')
    else:
        st.info("📊 Equity curve will appear once the first trades execute.")

    # ── Active Orders & Trade History ─────────────────────────────────────────
    col_left, col_right = st.columns([1, 1])

    with col_left:
        _section("Active Grid Orders", "🟦")

        if open_orders:
            buy_orders = [o for o in open_orders if o["side"] == "buy"]
            sell_orders = [o for o in open_orders if o["side"] == "sell"]

            order_data = []
            for o in sorted(open_orders, key=lambda x: x["price"], reverse=True):
                side_badge = (
                    '<span class="badge-sell">SELL</span>'
                    if o["side"] == "sell"
                    else '<span class="badge-buy">BUY</span>'
                )
                order_data.append({
                    "Side": o["side"].upper(),
                    "Price": f"${o['price']:,.2f}",
                    "Qty": f"{o['qty']:.6f}",
                    "Level": o.get("grid_level", "—"),
                    "Created": _ts_to_str(o["created_at"]),
                })

            st.dataframe(
                order_data,
                width='stretch',
                hide_index=True,
            )
            st.caption(
                f"🟢 {len(buy_orders)} buy orders  |  🔴 {len(sell_orders)} sell orders"
            )
        else:
            st.info("No active orders — waiting for grid to initialize.")

    with col_right:
        _section("Recent Trades", "📋")

        if recent_trades:
            trade_rows = []
            for t in recent_trades[:20]:
                pnl = t.get("realized_pnl", 0.0)
                trade_rows.append({
                    "Time": _ts_to_str(t["created_at"]),
                    "Side": t["side"].upper(),
                    "Fill Price": f"${t['fill_price']:,.2f}",
                    "Qty": f"{t['qty']:.6f}",
                    "Fee": f"${t['fee']:.4f}",
                    "PnL": f"{'+' if pnl >= 0 else ''}${pnl:.4f}",
                })
            st.dataframe(trade_rows, width='stretch', hide_index=True)
        else:
            st.info("No trades yet — bot is monitoring price levels.")

    # ── Grid visualization ────────────────────────────────────────────────────
    if center_price > 0 and open_orders:
        _section("Grid Visualization", "🗺️")

        prices_open = sorted({o["price"] for o in open_orders}, reverse=True)
        sides = {o["price"]: o["side"] for o in open_orders}

        fig2 = go.Figure()

        for price in prices_open:
            color = "#34d399" if sides[price] == "buy" else "#f87171"
            fig2.add_shape(
                type="line",
                x0=0, x1=1, y0=price, y1=price,
                xref="paper",
                line=dict(color=color, width=1.5, dash="dot"),
            )

        if center_price:
            fig2.add_shape(
                type="line",
                x0=0, x1=1, y0=center_price, y1=center_price,
                xref="paper",
                line=dict(color="#fbbf24", width=2),
            )
            fig2.add_annotation(
                x=1.01, y=center_price, xref="paper",
                text=f"Center ${center_price:,.0f}",
                showarrow=False,
                font=dict(color="#fbbf24", size=11),
                xanchor="left",
            )

        fig2.update_layout(
            template="plotly_dark",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(14,14,24,0.6)",
            height=250,
            margin=dict(l=10, r=120, t=10, b=10),
            xaxis=dict(visible=False),
            yaxis=dict(
                tickprefix="$",
                tickfont=dict(size=10, color="#64748b"),
                gridcolor="rgba(255,255,255,0.04)",
            ),
        )
        st.plotly_chart(fig2, width='stretch')

    # ── Footer ────────────────────────────────────────────────────────────────
    st.markdown(
        """
        <div style="margin-top:32px;padding:16px;border-top:1px solid rgba(255,255,255,0.06);
                    text-align:center;color:#475569;font-size:0.78rem;">
            🔒 Paper Trading Mode — No Real Funds at Risk &nbsp;|&nbsp;
            WhiteBIT DCA/Grid Bot &nbsp;|&nbsp;
            Auto-refreshing every 2 seconds
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Auto-refresh
    time.sleep(2)
    st.rerun()


if __name__ == "__main__":
    main()
