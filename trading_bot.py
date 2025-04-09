import os
import json
import logging
import requests
from alpaca_trade_api import REST as AlpacaRest
import alpaca_trade_api.errors as alpaca_errors
import yfinance as yf
from dotenv import load_dotenv
import os

# Import custom modules
from gemini_integration import (
    get_trending_stocks,
    build_gemini_prompt,
    call_gemini,
    save_gemini_history,
    parse_gemini_response
)
from validation import validate_trades

# Load environment variables from .env
load_dotenv()

ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
ALPACA_BASE_URL = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")

# Initialize Alpaca client.
alpaca_client = AlpacaRest(ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_BASE_URL)

logging.basicConfig(
    level=logging.INFO,
    filename='trading_bot.log',
    format='%(asctime)s:%(levelname)s:%(message)s'
)

def get_portfolio_info():
    try:
        account = alpaca_client.get_account()
        positions = alpaca_client.list_positions()
        positions_list = []
        for p in positions:
            positions_list.append({
                "ticker": p.symbol,
                "qty": p.qty,
                "unrealized_pl": p.unrealized_pl,
                "current_price": p.current_price,
            })
        portfolio_info = {
            "account_value": account.equity,
            "positions": positions_list,
        }
        return portfolio_info
    except Exception as e:
        logging.error(f"Error fetching portfolio info: {e}")
        return {}

def get_relevant_tickers(open_positions, trending_stocks):
    tickers = set()
    for pos in open_positions:
        tickers.add(pos["ticker"])
    for t in trending_stocks:
        tickers.add(t)
    return list(tickers)[:60]

def get_quote_data(tickers):
    headers = {"X-Finnhub-Token": FINNHUB_API_KEY}
    quote_data = {}
    for ticker in tickers:
        url = f"https://finnhub.io/api/v1/quote?symbol={ticker}"
        try:
            response = requests.get(url, headers=headers)
            if response.status_code == 200:
                quote_data[ticker] = response.json()  # Expect keys: c, h, l, o, pc
            else:
                logging.error(f"Failed to fetch quote for {ticker}: {response.status_code}")
        except Exception as e:
            logging.error(f"Error fetching quote for {ticker}: {e}")
    return quote_data

def get_last_gemini_history(n=3):
    from datetime import date
    folder = "gemini_history"
    today = date.today().isoformat()
    file_path = os.path.join(folder, f"{today}.json")
    if os.path.exists(file_path):
        with open(file_path, "r") as f:
            history = json.load(f)
        return history[-n:]
    else:
        return []

def main():
    # 1. Get trending stocks via Gemini.
    trending_stocks = get_trending_stocks()
    logging.info("Trending Stocks: " + str(trending_stocks))

    # 2. Get portfolio information from Alpaca.
    portfolio_info = get_portfolio_info()
    logging.info("Portfolio Info: " + json.dumps(portfolio_info, indent=2))

    # 3. Generate a list of relevant tickers (current positions + trending stocks).
    open_positions = portfolio_info.get("positions", [])
    relevant_tickers = get_relevant_tickers(open_positions, trending_stocks)
    logging.info("Relevant Tickers: " + str(relevant_tickers))

    # 4. Fetch market quotes from Finnhub.
    quote_data = get_quote_data(relevant_tickers)
    logging.info("Quote Data: " + json.dumps(quote_data, indent=2))

    # 5. Get the last 3 Gemini responses (for context).
    last_history = get_last_gemini_history()
    previous_plan = "\n".join(last_history) if last_history else ""

    # 6. Build the Gemini prompt.
    gemini_prompt = build_gemini_prompt(portfolio_info, quote_data, previous_plan)
    logging.info("Gemini Prompt: " + gemini_prompt)

    # 7. Call Gemini for trade actions.
    gemini_response = call_gemini(gemini_prompt)
    logging.info("Gemini Response: " + gemini_response)
    save_gemini_history(gemini_response)

    # 8. Parse Gemini response to extract trade actions.
    trades = parse_gemini_response(gemini_response)
    logging.info("Parsed Trade Actions: " + json.dumps(trades, indent=2))

    # 9. Validate trade actions.
    valid_trades = validate_trades(trades, quote_data, portfolio_info)
    logging.info("Valid Trade Actions: " + json.dumps(valid_trades, indent=2))

    # 10. Execute valid trades via Alpaca.
    for trade in valid_trades:
        ticker = trade.get("ticker")
        action = trade.get("action", "").upper()
        quantity = trade.get("quantity")
        order_target_price = trade.get("order_target_price")
        try:
            if order_target_price:
                if action in ["BUY", "COVER"]:
                    alpaca_client.submit_order(
                        symbol=ticker,
                        qty=quantity,
                        side="buy",
                        type="limit",
                        limit_price=order_target_price,
                        time_in_force="day"
                    )
                elif action in ["SELL", "SHORT"]:
                    alpaca_client.submit_order(
                        symbol=ticker,
                        qty=quantity,
                        side="sell",
                        type="limit",
                        limit_price=order_target_price,
                        time_in_force="day"
                    )
            else:
                if action in ["BUY", "COVER"]:
                    alpaca_client.submit_order(
                        symbol=ticker,
                        qty=quantity,
                        side="buy",
                        type="market",
                        time_in_force="day"
                    )
                elif action in ["SELL", "SHORT"]:
                    alpaca_client.submit_order(
                        symbol=ticker,
                        qty=quantity,
                        side="sell",
                        type="market",
                        time_in_force="day"
                    )
            logging.info(f"Placed order for {trade}")
        except Exception as e:
            logging.error(f"Error executing trade {trade}: {e}")

if __name__ == "__main__":
    main()
