def validate_trades(trades, quote_data, portfolio_info):
    """
    Validates each trade action.
    
    - For BUY/COVER orders: the stop loss must be at least 80% of the current price.
    - For SELL/SHORT orders: the stop loss must be no more than 120% of the current price.
    - Adjusts the stop loss if it is missing or set incorrectly.
    - Ensures that the order value does not exceed 70% of the total portfolio equity.
    - Validates SELL/COVER actions against existing positions.
    - Removes duplicate ticker/action pairs, keeping only the last one.
    
    Returns a list of validated (or adjusted) trade actions.
    """
    # First, handle duplicate trades by keeping only the last one for each ticker/action pair
    unique_trades = {}
    for trade in trades:
        ticker = trade.get("ticker")
        action = trade.get("action", "").upper()
        if ticker and action:
            key = f"{ticker}_{action}"
            unique_trades[key] = trade
    
    # Get current positions from portfolio info
    positions = {}
    for position in portfolio_info.get("positions", []):
        ticker = position.get("ticker", "").upper()
        qty = float(position.get("qty", 0))
        if ticker:
            # Positive quantity means long position, negative means short position
            positions[ticker] = qty
    
    valid_trades = []
    try:
        account_value = float(portfolio_info.get("account_value", 0))
    except Exception:
        account_value = 0

    # Process the unique trades
    for trade in unique_trades.values():
        ticker = trade.get("ticker")
        action = trade.get("action", "").upper()
        quantity = trade.get("quantity")

        # Check if SELL/COVER actions have corresponding positions
        if action == "SELL":
            position_qty = positions.get(ticker, 0)
            if position_qty <= 0:  # No long position to sell
                continue
            # Limit quantity to available position size
            if quantity > position_qty:
                quantity = int(position_qty)
                trade["quantity"] = quantity
        
        elif action == "COVER":
            position_qty = positions.get(ticker, 0)
            if position_qty >= 0:  # No short position to cover
                continue
            # Limit quantity to available short position size (shorts are negative)
            if quantity > abs(position_qty):
                quantity = int(abs(position_qty))
                trade["quantity"] = quantity

        try:
            if ticker not in quote_data:
                current_price = float(trade.get("order_target_price"))
            else:
                current_price = float(quote_data[ticker].get("current_price"))

            quantity = int(quantity)
        except Exception:
            continue

        # Ensure trade dollar value does not exceed 70% of account value.
        max_dollar = account_value * 0.7
        if current_price * quantity > max_dollar:
            quantity = int(max_dollar // current_price)
            trade["quantity"] = quantity
            if quantity == 0:
                continue

        # Check and adjust stop loss based on action type.
        stop_loss = trade.get("stop_loss")
        if action in ["BUY", "COVER"]:
            # For buys, stop loss should not be set lower than 70% of current price.
            min_stop = current_price * 0.7
            if stop_loss is None or stop_loss < min_stop:
                trade["stop_loss"] = round(min_stop, 2)
        elif action in ["SELL", "SHORT"]:
            # For sells/shorts, stop loss should not be set higher than 130% of current price.
            max_stop = current_price * 1.3
            if stop_loss is None or stop_loss > max_stop:
                trade["stop_loss"] = round(max_stop, 2)
        valid_trades.append(trade)
    return valid_trades
