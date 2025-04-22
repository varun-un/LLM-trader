import streamlit as st
from dotenv import load_dotenv
import matplotlib.pyplot as plt
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetPortfolioHistoryRequest
from datetime import datetime
import os

load_dotenv()

# Setup (replace with your own or use env vars)
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
PAPER = True  # set to False for live trading account

trading_client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=PAPER)

# Mapping of user-friendly labels to API-supported timeframes
TIMEFRAME_OPTIONS = {
    "1 Day": "1D",
    "5 Days": "5D",
    "10 Days": "10D",
    "1 Month": "1M",
    "3 Months": "3M",
    "1 Year": "1Y"
}

# UI
st.title("📈 Alpaca Portfolio History Viewer")
selected_label = st.selectbox("Select Timeframe", list(TIMEFRAME_OPTIONS.keys()))
selected_timeframe = TIMEFRAME_OPTIONS[selected_label]

# Fetch data
with st.spinner("Fetching portfolio data..."):
    request_params = GetPortfolioHistoryRequest(
        period=selected_timeframe,
        # granularity (1Min, 15Min, 1H, 1D)
        timeframe="1D" if selected_timeframe in ["1M", "3M", "1Y"] else ("1H" if selected_timeframe in ["10D", "5D"] else "1Min"),
        extended_hours=False
    )
    try:
        history = trading_client.get_portfolio_history(request_params)
        timestamps = [datetime.fromtimestamp(ts) for ts in history.timestamp]
        equity = history.equity

        # Plot
        fig, ax = plt.subplots()
        ax.plot(timestamps, equity, marker="o", linestyle="-", linewidth=1, markersize=1.5)
        ax.set_title(f"Portfolio Value Over {selected_label}")
        ax.set_xlabel("Time")
        ax.set_ylabel("Equity ($)")
        ax.grid(True)
        plt.xticks(rotation=60)  # Rotate x-axis labels for better readability
        st.pyplot(fig)
    except Exception as e:
        st.error(f"Failed to fetch portfolio history: {e}")


# TO RUN:
# streamlit run portfolio_viewer.py

