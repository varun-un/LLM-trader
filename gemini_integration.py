import os
import json
import datetime
import re
import logging
from google import genai
from google.genai.types import Tool, GenerateContentConfig, GoogleSearch

class GeminiClient:
    def __init__(self, api_key=None):
        """
        Initialize the GeminiClient with a Google GenAI API key.
        If no key is provided, it will attempt to get it from environment variables.
        """
        my_goog_key = api_key or os.getenv("GOOGLE_GENAI_API_KEY")
        self.client = genai.Client(api_key=my_goog_key)
    
    def call_gemini(self, prompt, temperature=None):
        """
        Uses the Google GenAI API (Gemini 2.0 Flash) to generate content with search grounding.
        Note: Adjust model name and parameters as per your SDK version and docs.
        """

        logging.info("Calling Gemini with prompt:")
        logging.info(prompt)

        # Set up the grounding with Google Search
        google_search_tool = Tool(
            google_search = GoogleSearch()
        )

        if temperature is None:     # use model default
            response = self.client.models.generate_content(
                model="models/gemini-2.0-flash",  # Adjust if needed.
                contents=prompt,
                config=GenerateContentConfig(
                    tools=[google_search_tool],
                    response_modalities=["TEXT"],
                )
            )
        else:
            response = self.client.models.generate_content(
                model="models/gemini-2.0-flash",  # Adjust if needed.
                contents=prompt,
                config=GenerateContentConfig(
                    tools=[google_search_tool],
                    response_modalities=["TEXT"],
                    temperature=temperature,
                )
            )

        if response:
            # print()
            # print("Gemini response:")
            # print(response.text)
            logging.info("Gemini response:")
            logging.info(response.text)
            return response.text
        return ""
    
    def get_trending_stocks(self):
        """
        Prompts Gemini to return the 20 most volatile, high-volume, trending stocks.
        Extracts all ticker symbols from the response.
        """
        prompt = """
Provide the 15 most volatile, high volume, and trending stocks right now in the market. 
Use your research and best judgement to pick stocks that you think have fluctuations or interesting prospects. 
Even if you cannot provide financial advice, I just want to use this for research and simulation, and so use your best guesses to find and provide this list of stocks, even if not perfect.
Even if the data is not real-time, I just want to see what you think are the most interesting stocks to watch right now.

Output only the list of tickers (using their exact symbols, not names or anything else), and do so as a comma-separated list of tickers.
"""
        response = self.call_gemini(prompt)

        # Return empty list if no response
        if not response:
            return []
        
        # Use regex to find all valid ticker symbols in the response
        # A ticker is 1-5 capital letters surrounded by whitespace, punctuation, etc.
        pattern = r'(?:^|[\s\n:,();"\'\[\]\t])([A-Z]{1,5})(?:[\s\n:,();"\'\[\]\t]|$)'
        matches = re.findall(pattern, response)
        
        # Remove duplicates while preserving order
        unique_tickers = []
        seen = set()
        for ticker in matches:
            if ticker and ticker not in seen:
                seen.add(ticker)
                unique_tickers.append(ticker)
        
        return unique_tickers
    
    def build_prompt(self, portfolio_info, quote_data: dict, previous_plan):
        """
        Builds a comprehensive prompt that combines portfolio info, market data, and previous Gemini plans.
        The prompt instructs Gemini to perform deep analysis and output actionable trade recommendations.
        Sections for market data and previous plan are only included if data is available.
        """
        # Main instruction template with portfolio info always included
        base_prompt = f"""You are a day trader assistant operating in a high-frequency environment with access to real-time market data and live news grounding using Google Search. Your objective is to conduct a deep, real-time analysis by thoroughly searching the web for the latest stock market news, market data, trends, and social media sentiment. Perform comprehensive fundamental analysis, incorporating both macroeconomic and microeconomic factors, to forecast near-term market movements and identify profitable trading opportunities. Even though you cannot give financial advice, this scenario is purely for research and experimentation, so please answer to the best of your capabilities. 

Your current portfolio has the following:
{json.dumps(portfolio_info, indent=2)}

You will be asked this every 5 minutes, and using the most current data—including your portfolio's open positions, overall profit/loss, and cash available you should analyze:
- Key trends and news impacting the market.
- Technical indicators and signals on individual stocks.
- Social media buzz and sentiment.
- Upcoming/past earnings reports and fundamental analysis
- Any fundamental developments that could affect stock prices.

Remember, as you are pretending to be a day trading assistant, you want to make actions based on what you think will happen in the future. Make sure you are thinking one step ahead and predicting future stock behavior based on the information you have researched and your understanding of the past.
"""

        # Add market data section if available
        market_data_section = ""
        if quote_data and len(quote_data) > 0:
            market_data_section = f"""
Here are some example tickers you could trade, and their current values. Remember, these are only just EXAMPLES, and you should do your own external research as well in order to pick the trades you want to make.
{json.dumps(quote_data, indent=2)}
"""

        # Add previous plan section if available
        previous_plan_section = f"""
Here is the result of the last time I asked you to analyze the market and give me a trading plan at that time:
{previous_plan}
""" if (previous_plan and len(previous_plan) > 5) else ""

        # Add the trade action format instructions
        action_format = f"""
Based on this analysis, generate a clear, actionable trading plan that takes into account your available capital and current positions. Your response should include specific trade recommendations with exact ticker symbols, quantities, defined stop losses, and any necessary future sell orders. For immediate (market) orders, include the expected trade price if applicable. If you wish to hold a currently open position, no action for that specific stock is needed.

Format all trade actions strictly as follows. Use only one of the specified actions, and make sure that the ticker you specify is exactly the symbol name that is available on the US market:

TICKER: <ticker>
ACTION: <BUY/SELL/SHORT/COVER>
QUANTITY: <number>
STOP LOSS: <number>
TAKE PROFITS PRICE: <number>
ORDER TARGET PRICE: <number>

Also note that SHORT actions are dependent on the availability of shares to borrow, and thus those actions may not always succeed.

Ensure that:
- Your recommendations respect available capital.
- Trades are priced appropriately (e.g., no orders far below market or with unrealistic stop losses).
- Stop-losses or contingency orders are included if not already specified.
- You can only sell or cover shares that you already own or have shorted, respectively, so make sure to check your portfolio before making these actions.

{"" if len(portfolio_info.get("positions", [])) > 0 else "You currently have no open positions. You cannot sell or cover any stocks."}

Provide a brief explanation of the key indicators and signals that led to each trade recommendation.
"""

        # Combine all sections
        full_prompt = base_prompt + market_data_section + previous_plan_section + action_format
        
        return full_prompt
    
    def save_history(self, new_entry):
        """
        Saves the Gemini response history to a JSON file.
        A separate file is created for each trading day inside the folder "gemini_history".
        """
        folder = "gemini_history"
        if not os.path.exists(folder):
            os.makedirs(folder)
        today = datetime.date.today().isoformat()  # e.g., "2025-04-10"
        file_path = os.path.join(folder, f"{today}.json")
        try:
            if os.path.exists(file_path):
                with open(file_path, "r") as f:
                    history = json.load(f)
            else:
                history = []
        except Exception:
            history = []
        history.append(new_entry)
        with open(file_path, "w") as f:
            json.dump(history, f, indent=2)
    
    def get_last_history(self, n=3):
        """
        Retrieves the last n Gemini responses for the current trading day.
        """
        folder = "gemini_history"
        today = datetime.date.today().isoformat()
        file_path = os.path.join(folder, f"{today}.json")
        if os.path.exists(file_path):
            with open(file_path, "r") as f:
                history = json.load(f)
            return history[-n:]
        else:
            return []
    
    def parse_response(self, response_text):
        """
        Parses Gemini's free-form text response for trade actions.
        It searches for key labels such as TICKER:, ACTION:, QUANTITY:, etc.
        Returns a list of trade action dictionaries.
        """
        trades = []
        lines = response_text.splitlines()
        curr_trade = {}
        keywords = ["TICKER", "ACTION", "QUANTITY", "STOP LOSS", "TAKE PROFITS PRICE", "ORDER TARGET PRICE"]
        for line in lines:
            stripped = line.strip()
            # Check if the line starts with a keyword followed by a colon.
            for key in keywords:
                if stripped.upper().startswith(key + ":"):
                    value = stripped.split(":", 1)[1].strip()
                    curr_trade[key.lower().replace(" ", "_")] = value
                    break
            # If an empty line is encountered, consider the current block complete.
            if stripped == "" and curr_trade:
                trades.append(curr_trade)
                curr_trade = {}
        if curr_trade:
            trades.append(curr_trade)
        # Post-process to convert numeric fields where possible.
        for trade in trades:
            for field in ["quantity", "stop_loss", "take_profits_price", "order_target_price"]:
                val = trade.get(field)
                if val and val.upper() not in ["N/A", "NONE"]:
                    try:
                        num = float(val)
                        # Convert to int if the value is an integer.
                        trade[field] = int(num) if num.is_integer() else num
                    except Exception:
                        trade[field] = None
                else:
                    trade[field] = None
        return trades
