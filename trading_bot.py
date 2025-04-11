import os
import json
import logging
import requests
from dotenv import load_dotenv
import os
import datetime

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

# configure logging
if not os.path.exists('C:\\Users\\varun\\Documents\\Python\\LLM-trader\\logs'):
    os.makedirs('C:\\Users\\varun\\Documents\\Python\\LLM-trader\\logs')

# get today's date
today = datetime.datetime.today().strftime('%Y-%m-%d')

logging.basicConfig(
    level=logging.INFO,
    filename=f'C:\\Users\\varun\\Documents\\Python\\LLM-trader\\logs\\{today}.log',
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
                "unrealized_profit_loss": pos.unrealized_pl,
                "current_price": pos.current_price,
            })
        portfolio_info = {
            "account_value": account.equity,
            "cash_balance": account.cash,
            "buying_power": account.non_marginable_buying_power,    # constrain LLM to only use cash - no margin
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

def cancel_bracket_orders_for_ticker(ticker: str):
    """
    Searches for open bracket orders for the given ticker and cancels them.
    Saves the stop loss and take profit values from the first found bracket order.
    
    Returns:
        A tuple (total_reserved, original_bracket) where total_reserved is
        the total number of shares reserved by the bracket orders, and
        original_bracket is a dictionary of the original bracket details, e.g.:
            {"stop_loss": value, "take_profit": value}
        If no bracket order is found, returns (0, None).
    """
    total_reserved = 0
    original_bracket = None
    try:
        open_orders = trading_client.get_all_orders(status="open", symbols=[ticker])
        for order in open_orders:
            # Check if this is a bracket order. Adjust the attribute check based on your SDK.
            if hasattr(order, "order_class") and order.order_class == OrderClass.BRACKET.value:
                total_reserved += int(order.qty)
                if original_bracket is None:
                    # Here we assume the order object includes stop loss and take profit details.
                    # You might need to adjust these attribute names based on Alpaca-py's actual order schema.
                    try:
                        orig_stop_loss = float(order.stop_loss["stop_price"])
                        orig_take_profit = float(order.take_profit["limit_price"])
                        original_bracket = {
                            "stop_loss": orig_stop_loss,
                            "take_profit": orig_take_profit
                        }
                    except Exception as parse_err:
                        logging.error(f"Error parsing original bracket details for {ticker}: {parse_err}")
                # Cancel this bracket order.
                cancel_resp = trading_client.cancel_order(order.id)
                logging.info(f"Canceled bracket order {order.id} for {ticker}: {cancel_resp}")
    except Exception as e:
        logging.error(f"Error canceling bracket orders for {ticker}: {e}")
    return total_reserved, original_bracket

def reestablish_bracket_for_remaining_shares(ticker: str, remaining_qty: int, original_bracket: dict, side: OrderSide):
    """
    Re-establishes a new bracket order for the remaining short position using the original
    stop loss and take profit values.
    
    Parameters:
        ticker (str): The ticker symbol.
        remaining_qty (int): The number of shares remaining short.
        original_bracket (dict): A dictionary with keys "stop_loss" and "take_profit" from the original order.
        side (OrderSide): The order side; should be OrderSide.SELL for covering shorts.
    
    Returns:
        The response from trading_client.submit_order(), or None if an error occurs.
    """
    try:
        # Use the saved values from the original bracket.
        stop_loss_value = float(original_bracket.get("stop_loss"))
        take_profit_value = float(original_bracket.get("take_profit"))
        
        # Calculate limit_loss_price using the same logic as before.
        if side == OrderSide.SELL:
            limit_loss_price = round(stop_loss_value + (stop_loss_value * 0.01), 2)
        else:
            limit_loss_price = round(stop_loss_value - (stop_loss_value * 0.01), 2)
        
        bracket_order = MarketOrderRequest(
            symbol=ticker,
            qty=remaining_qty,
            side=side,
            type=OrderType.MARKET,         # Market order for entry.
            time_in_force=TimeInForce.GTC,
            order_class=OrderClass.BRACKET,
            take_profit={"limit_price": take_profit_value},
            stop_loss={"stop_price": stop_loss_value, "limit_price": limit_loss_price}
        )
        new_order = trading_client.submit_order(order_data=bracket_order)
        logging.info(f"Re-established bracket order for {ticker} with qty {remaining_qty}: {new_order}")
        return new_order
    except Exception as e:
        logging.error(f"Error re-establishing bracket order for {ticker}: {e}")
        return None

def execute_trade(order_dict: dict):
    """
    Places an order using Alpaca's trading_client based on the provided order dictionary.
    
    Parameters:
        order_dict (dict): Contains:
            - 'ticker' (str): e.g., 'NVDA'
            - 'action' (str): one of 'BUY', 'SELL', 'SHORT', 'COVER'
            - 'quantity' (int): number of shares
            - 'stop_loss' (float): stop loss trigger price (used with BUY/SHORT)
            - 'take_profits_price' (float): take profit limit price (used with BUY/SHORT)
            - 'order_target_price' (float): target price (ignored for BUY/SHORT and SELL/COVER)
    
    Returns:
        The response from trading_client.submit_order().
    """
    ticker = order_dict.get("ticker").upper()
    action = order_dict.get("action", "").upper()
    qty = order_dict.get("quantity")
    
    # Determine order side.
    if action in ["BUY", "COVER"]:
        order_side = OrderSide.BUY
    elif action in ["SELL", "SHORT"]:
        order_side = OrderSide.SELL
    else:
        raise ValueError("Invalid action. Must be one of: BUY, SELL, SHORT, COVER")
    
    if action in ["BUY", "SHORT"]:
        # For entry orders (BUY/SHORT), place a bracket order.
        stop_loss_value = order_dict.get("stop_loss")
        take_profit_value = order_dict.get("take_profits_price")
        if action == "SHORT":
            limit_loss_price = round(stop_loss_value + (stop_loss_value * 0.01), 2)
        else:
            limit_loss_price = round(stop_loss_value - (stop_loss_value * 0.01), 2)
    
        stop_loss_value = round(stop_loss_value, 2)
        take_profit_value = round(take_profit_value, 2)
    
        bracket_order = MarketOrderRequest(
            symbol=ticker,
            qty=qty,
            side=order_side,
            type=OrderType.MARKET,
            time_in_force=TimeInForce.GTC,
            order_class=OrderClass.BRACKET,
            take_profit={"limit_price": take_profit_value},
            stop_loss={"stop_price": stop_loss_value, "limit_price": limit_loss_price}
        )
        try:
            response = trading_client.submit_order(order_data=bracket_order)
            logging.info(f"Placed bracket order for {ticker}: {response}")
            return response
        except Exception as e:
            logging.error(f"Error executing trade for {order_dict}: {e}")
            return None

    elif action in ["SELL", "COVER"]:
        if action == "COVER":
            # For COVER orders, cancel existing bracket orders and capture the original bracket data.
            reserved_qty, original_bracket = cancel_bracket_orders_for_ticker(ticker)
            logging.info(f"Canceled bracket orders for {ticker}; reserved shares: {reserved_qty}; original bracket: {original_bracket}")
            # If the short position held by bracket orders exceeds the COVER qty,
            # re-establish a bracket order for the remaining shares.
            if reserved_qty > qty and original_bracket is not None:
                remaining_qty = reserved_qty - qty
                # Use the original bracket's stop loss and take profit.
                reestablish_bracket_for_remaining_shares(ticker, remaining_qty, original_bracket, order_side)
        # Submit a simple market order for SELL or COVER.
        market_order = MarketOrderRequest(
            symbol=ticker,
            qty=qty,
            side=order_side,
            type=OrderType.MARKET,
            time_in_force=TimeInForce.GTC
        )
        try:
            response = trading_client.submit_order(order_data=market_order)
            logging.info(f"Placed market order for {ticker}: {response}")
            return response
        except Exception as e:
            logging.error(f"Error executing trade for {order_dict}: {e}")
            return None


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

    # 5. Retrieve the last 5 Gemini responses for context.
    how_many_to_get = 5
    last_history, history_times = gemini_client.get_last_history(how_many_to_get)
    previous_plan = ""
    for idx, history in enumerate(last_history):
        if history_times[idx] == 'w':
            previous_plan += f"Summary of plan from last week before close: \n{history}\n"
        elif history_times[idx] == 'd':
            previous_plan += f"Summary of plan from yesterday before close: \n{history}\n"
        else:
            previous_plan += f"Summary of plan from {(how_many_to_get - idx) * 5} minutes ago: \n{history}\n"

    # 6. Build the Gemini prompt.
    gemini_prompt = gemini_client.build_prompt(portfolio_info, quote_data, previous_plan)

    # 7. Call Gemini to get the proposed trade actions.
    gemini_response = gemini_client.call_gemini(gemini_prompt)
    gemini_client.save_history(gemini_response)

    # 8. Parse the Gemini response.
    trades = gemini_client.parse_response(gemini_response)
    logging.info("Parsed Trade Actions: " + json.dumps(trades, indent=2))

    # 9. Validate the trade actions.
    valid_trades = validate_trades(trades, quote_data, portfolio_info, FINNHUB_API_KEY)
    logging.info("Valid Trade Actions: " + json.dumps(valid_trades, indent=2))

    # 10. Execute each valid trade via Alpaca.
    for trade in valid_trades:
        try:
            execute_trade(trade)
            logging.info(f"Executed trade: {' '.join([f'{k}:{v}' for k, v in trade.items()])}")
        except Exception as e:
            logging.error(f"Error executing trade {trade}: {e}")

if __name__ == "__main__":
    main()
