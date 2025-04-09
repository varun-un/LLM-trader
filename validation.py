def validate_trades(trades, quote_data, portfolio_info):
    """
    Validates each trade action.
    
    - For BUY/COVER orders: the stop loss must be at least 80% of the current price.
    - For SELL/SHORT orders: the stop loss must be no more than 120% of the current price.
    - Adjusts the stop loss if it is missing or set incorrectly.
    - Also ensures that the order value does not exceed 50% of the total portfolio equity.
    Returns a list of validated (or adjusted) trade actions.
    """
    valid_trades = []
    try:
        account_value = float(portfolio_info.get("account_value", 0))
    except Exception:
        account_value = 0
    for trade in trades:
        ticker = trade.get("ticker")
        action = trade.get("action", "").upper()
        quantity = trade.get("quantity")
        if not ticker or ticker not in quote_data or quantity is None:
            continue
        try:
            current_price = float(quote_data[ticker].get("c"))
            quantity = int(quantity)
        except Exception:
            continue

        # Ensure trade dollar value does not exceed 50% of account value.
        max_dollar = account_value * 0.5
        if current_price * quantity > max_dollar:
            quantity = int(max_dollar // current_price)
            trade["quantity"] = quantity
            if quantity == 0:
                continue

        # Check and adjust stop loss based on action type.
        stop_loss = trade.get("stop_loss")
        if action in ["BUY", "COVER"]:
            # For buys, stop loss should not be set lower than 80% of current price.
            min_stop = current_price * 0.8
            if stop_loss is None or stop_loss < min_stop:
                trade["stop_loss"] = round(min_stop, 2)
        elif action in ["SELL", "SHORT"]:
            # For sells/shorts, stop loss should not be set higher than 120% of current price.
            max_stop = current_price * 1.2
            if stop_loss is None or stop_loss > max_stop:
                trade["stop_loss"] = round(max_stop, 2)
        valid_trades.append(trade)
    return valid_trades
