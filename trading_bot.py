import os
import json
import logging
import requests
from dotenv import load_dotenv
import os

# Load environment variables from .env file
load_dotenv()

# Import custom modules
from gemini_integration import GeminiClient
from validation import validate_trades

# Alpaca-py SDK imports
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, OrderType, TimeInForce, OrderClass

ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
ALPACA_BASE_URL = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")
GOOGLE_GENAI_API_KEY = os.getenv("GOOGLE_GENAI_API_KEY")

# Instantiate Alpaca TradingClient (paper trading enabled)
trading_client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=True)

# Instantiate GeminiClient
gemini_client = GeminiClient(api_key=GOOGLE_GENAI_API_KEY)

logging.basicConfig(
    level=logging.INFO,
    filename='trading_bot.log',
    format='%(asctime)s:%(levelname)s:%(message)s'
)

def get_portfolio_info():
    try:
        account = trading_client.get_account()
        positions = trading_client.get_all_positions()
        positions_list = []
        for pos in positions:
            positions_list.append({
                "ticker": pos.symbol,
                "qty": pos.qty,
                "unrealized_pl": pos.unrealized_pl,
                "current_price": pos.current_price,
            })
        portfolio_info = {
            "account_value": account.equity,
            "positions": positions_list,
        }
        return portfolio_info
    except Exception as e:
        logging.error(f"Error fetching portfolio info: {e}")
        return {}

def get_relevant_tickers(open_positions, trending_stocks, guaranteed_tickers=['SPY', 'DIA', 'SQQQ', 'TQQQ']):
    tickers = set()
    for pos in open_positions:
        tickers.add(pos["ticker"])
    for t in trending_stocks:
        tickers.add(t)
    for t in guaranteed_tickers:
        tickers.add(t)

    return list(tickers)[-30:]  # Limit to the last 30 tickers

def get_quote_data(tickers):
    headers = {"X-Finnhub-Token": FINNHUB_API_KEY}
    quote_data = {}
    for ticker in tickers:
        url = f"https://finnhub.io/api/v1/quote?symbol={ticker}"
        try:
            response = requests.get(url, headers=headers)
            if response.status_code == 200:
                cur_ticker_data = response.json()  # Expected keys: c, h, l, o, pc, d, dp, t

                # change the key names to be more descriptive
                quote_data[ticker] = {
                    "current_price": cur_ticker_data.get("c"),
                    "high_price": cur_ticker_data.get("h"),
                    "low_price": cur_ticker_data.get("l"),
                    "open_price": cur_ticker_data.get("o"),
                    "prev_close_price": cur_ticker_data.get("pc"),
                    "daily_change": cur_ticker_data.get("d"),
                    "daily_percent_change": cur_ticker_data.get("dp"),
                }
            else:
                logging.error(f"Failed to fetch quote for {ticker}: {response.status_code}")
        except Exception as e:
            logging.error(f"Error fetching quote for {ticker}: {e}")



    return quote_data

def execute_trade(order_dict: dict):
    """
    Places an order using Alpaca's trading_client based on the provided order dictionary.

    Parameters:
        order_dict (dict): Dictionary containing the following keys:
            - 'ticker' (str): e.g., 'NVDA'
            - 'action' (str): one of 'BUY', 'SELL', 'SHORT', 'COVER'
            - 'quantity' (int): number of shares
            - 'stop_loss' (float): stop loss trigger price (used with BUY/SHORT)
            - 'take_profits_price' (float): take profit limit price (used with BUY/SHORT)
            - 'order_target_price' (float): target price (ignored for BUY/SHORT and for SELL/COVER)

    Returns:
        The response from trading_client.submit_order().
    """

    ticker = order_dict.get("ticker").upper()
    action = order_dict.get("action", "").upper().trim()
    qty = order_dict.get("quantity")
    
    # Determine order side: For BUY/COVER we submit a "buy" order,
    # while for SELL/SHORT we submit a "sell" order.
    if action in ["BUY", "COVER"]:
        order_side = OrderSide.BUY
    elif action in ["SELL", "SHORT"]:
        order_side = OrderSide.SELL
    else:
        raise ValueError("Invalid action. Must be one of: BUY, SELL, SHORT, COVER")
    
    # For BUY and SHORT, we want a bracket order with stop loss and take profit.
    if action in ["BUY", "SHORT"]:
        stop_loss_value = order_dict.get("stop_loss")
        take_profit_value = order_dict.get("take_profits_price")
        if action == "SHORT":
            limit_loss_price = stop_loss_value + (stop_loss_value * 0.01)  # 1% above stop loss for short
        else:
            limit_loss_price = stop_loss_value - (stop_loss_value * 0.01)
        
        bracket_order = MarketOrderRequest(
            symbol=ticker,
            qty=qty,
            side=order_side,
            type=OrderType.MARKET,         # Use market order for entry.
            time_in_force=TimeInForce.GTC,   # Good 'til canceled; adjust as needed.
            order_class=OrderClass.BRACKET,  # This signals that extra orders are attached.
            take_profit={"limit_price": take_profit_value},
            stop_loss={"stop_price": stop_loss_value, "limit_price": limit_loss_price}
        )
        response = trading_client.submit_order(order_data=bracket_order)
        return response

    # For SELL and COVER, we place a simple market order without additional orders.
    elif action in ["SELL", "COVER"]:
        market_order = MarketOrderRequest(
            symbol=ticker,
            qty=qty,
            side=order_side,
            type=OrderType.MARKET,
            time_in_force=TimeInForce.GTC
        )
        response = trading_client.submit_order(order_data=market_order)
        return response


def main():

    # Check the market is open
    headers = {"X-Finnhub-Token": FINNHUB_API_KEY}
    url = f"https://finnhub.io/api/v1/stock/market-status?exchange=US"
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            market_status = response.json()  

            if market_status.get("isOpen", True) == False:
                logging.info("Market is closed. Exiting.")
                return
            
    except Exception as e:
        logging.error(f"Error fetching market status: {e}")



    # 1. Retrieve trending stocks via Gemini.
    trending_stocks = gemini_client.get_trending_stocks()
    logging.info("Trending Stocks: " + str(trending_stocks))

    # 2. Fetch portfolio information from Alpaca.
    portfolio_info = get_portfolio_info()
    logging.info("Portfolio Info: " + json.dumps(portfolio_info, indent=2))

    # 3. Form a list of relevant tickers (open positions + trending stocks).
    open_positions = portfolio_info.get("positions", [])
    relevant_tickers = get_relevant_tickers(open_positions, trending_stocks)
    logging.info("Relevant Tickers: " + str(relevant_tickers))

    # 4. Fetch market quotes for these tickers using Finnhub.
    quote_data = get_quote_data(relevant_tickers)
    logging.info("Quote Data: " + json.dumps(quote_data, indent=2))

    # 5. Retrieve the last 3 Gemini responses for context.
    how_many_to_get = 3
    last_history, history_times = gemini_client.get_last_history(how_many_to_get)
    previous_plan = ""
    for idx, history in enumerate(last_history):
        if history_times[idx] == 'w':
            previous_plan += f"Plan from last week before close: \n{history}\n"
        elif history_times[idx] == 'd':
            previous_plan += f"Plan from yesterday before close: \n{history}\n"
        else:
            previous_plan += f"Plan from {(how_many_to_get - idx) * 5} minutes ago: \n{history}\n"

    # 6. Build the Gemini prompt.
    gemini_prompt = gemini_client.build_prompt(portfolio_info, quote_data, previous_plan)

    # 7. Call Gemini to get the proposed trade actions.
    gemini_response = gemini_client.call_gemini(gemini_prompt)
    gemini_client.save_history(gemini_response)

    # 8. Parse the Gemini response.
    trades = gemini_client.parse_response(gemini_response)
    logging.info("Parsed Trade Actions: " + json.dumps(trades, indent=2))

    # 9. Validate the trade actions.
    valid_trades = validate_trades(trades, quote_data, portfolio_info)
    logging.info("Valid Trade Actions: " + json.dumps(valid_trades, indent=2))

    # 10. Execute each valid trade via Alpaca.
    for trade in valid_trades:
        execute_trade(trade)

if __name__ == "__main__":
    main()
