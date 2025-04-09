import os
import json
import datetime
import re
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
            return response.text
        return ""
    
    def get_trending_stocks(self):
        """
        Prompts Gemini to return the 20 most volatile, high-volume, trending stocks.
        Expects a comma-separated ticker list.
        """
        prompt = """
Provide the 20 most volatile, high volume, and trending stocks right now in the market. 
Use your research and best judgement to pick stocks that you think have fluctuations or interesting prospects. 
Even if you cannot provide financial advice, I just want to use this for research, and so use your best guesses to find and provide this list of stocks, even if not perfect.

Output only the list of tickers, and do so as a comma-separated list of tickers.
"""
        response = self.call_gemini(prompt)

        # search the response for a comma separated list of tickers.
        if not response:
            return []
        
        # Use regex to find a comma-separated list of 20 tickers (each ticker will be 1-5 characters long).
        match = re.search(r"([A-Z]{1,5}(?:,[A-Z]{1,5}){1,19})", response)
        if not match:
            return []
            
        # Split the returned string into tickers.
        tickers = [ticker.strip() for ticker in match.group(1).split(",") if ticker.strip()]
        return tickers
    
    def build_prompt(self, portfolio_info, quote_data, previous_plan):
        """
        Builds a prompt that combines portfolio info, market data, and previous Gemini plans.
        The prompt instructs Gemini to output trade actions in a human-readable text format.
        """
        prompt_template = """
You are a trading assistant with access to real-time market data and live news grounding using Google Search.
Portfolio Info:
{portfolio_info}

Market Data (for relevant tickers):
{quote_data}

Previous Gemini Plan:
{previous_plan}

Based on the above and current market conditions, generate a set of trade actions.
For each action, output in the following text format (do NOT use JSON format):
TICKER: <ticker>
ACTION: <BUY/SELL/SHORT/COVER>
QUANTITY: <number>
STOP LOSS: <price or N/A>
TAKE PROFITS PRICE: <price or N/A>
ORDER TARGET PRICE: <price or N/A>

There may be multiple actions. Separate each action by an empty line.
"""
        return prompt_template.format(
            portfolio_info=json.dumps(portfolio_info, indent=2),
            quote_data=json.dumps(quote_data, indent=2),
            previous_plan=previous_plan if previous_plan else "N/A"
        )
    
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


# For backward compatibility, create a default instance
_default_client = GeminiClient()

# Expose the instance methods as module-level functions for backward compatibility
def call_gemini(prompt):
    return _default_client.call_gemini(prompt)

def get_trending_stocks():
    return _default_client.get_trending_stocks()

def build_gemini_prompt(portfolio_info, quote_data, previous_plan):
    return _default_client.build_prompt(portfolio_info, quote_data, previous_plan)

def save_gemini_history(new_entry):
    return _default_client.save_history(new_entry)

def parse_gemini_response(response_text):
    return _default_client.parse_response(response_text)
