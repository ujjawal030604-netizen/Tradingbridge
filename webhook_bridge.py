"""
TradingView -> Alpaca Webhook Bridge
--------------------------------------
This script runs a small web server that:
1. Listens for incoming alerts from TradingView (sent as JSON via webhook)
2. Reads the action (buy/sell), symbol, and stop-loss/take-profit prices
3. Places a matching BRACKET order on Alpaca (entry + stop loss + take profit
   all submitted together, so your risk management actually carries over)

You do NOT need to be a programmer to run this -- just follow the setup
instructions in the README section at the bottom of this file.
"""

import os
import logging
from flask import Flask, request, jsonify
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, TakeProfitRequest, StopLossRequest
from alpaca.trading.enums import OrderSide, TimeInForce

# ---------------------------------------------------------------
# CONFIG -- pulled from environment variables so your keys are
# never hard-coded or accidentally shared/committed anywhere.
# ---------------------------------------------------------------
API_KEY = os.environ.get("ALPACA_API_KEY")
API_SECRET = os.environ.get("ALPACA_API_SECRET")
PAPER_TRADING = os.environ.get("ALPACA_PAPER", "true").lower() == "true"
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "changeme123")  # simple shared password

if not API_KEY or not API_SECRET:
    raise RuntimeError(
        "Missing ALPACA_API_KEY or ALPACA_API_SECRET environment variables. "
        "Set these before running the server."
    )

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("bridge")

app = Flask(__name__)
trading_client = TradingClient(API_KEY, API_SECRET, paper=PAPER_TRADING)


@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(force=True, silent=True)

    if data is None:
        log.warning("Received a non-JSON or empty request body.")
        return jsonify({"error": "invalid or missing JSON body"}), 400

    # --- Security check: reject anyone who doesn't know your secret ---
    if data.get("secret") != WEBHOOK_SECRET:
        log.warning("Rejected webhook: bad or missing secret.")
        return jsonify({"error": "unauthorized"}), 401

    try:
        symbol = data["ticker"]
        action = data["action"].lower()          # expects "buy" or "sell"
        qty = float(data.get("qty", 1))
        stop_loss_price = float(data["stop_loss"])
        take_profit_price = float(data["take_profit"])
    except (KeyError, ValueError, TypeError) as e:
        log.error(f"Malformed alert payload: {data} -- {e}")
        return jsonify({"error": f"missing or bad field: {e}"}), 400

    if action == "buy":
        side = OrderSide.BUY
    elif action == "sell":
        # "sell" here is used to close/exit -- for this simple long-only
        # setup we just close the open position rather than opening a short.
        try:
            trading_client.close_position(symbol)
            log.info(f"Closed position for {symbol}.")
            return jsonify({"status": "position closed", "symbol": symbol}), 200
        except Exception as e:
            log.error(f"Error closing position for {symbol}: {e}")
            return jsonify({"error": str(e)}), 500
    else:
        return jsonify({"error": f"unknown action '{action}'"}), 400

    # --- Build a bracket order: entry + stop loss + take profit together ---
    order_request = MarketOrderRequest(
        symbol=symbol,
        qty=qty,
        side=side,
        time_in_force=TimeInForce.DAY,
        order_class="bracket",
        take_profit=TakeProfitRequest(limit_price=round(take_profit_price, 2)),
        stop_loss=StopLossRequest(stop_price=round(stop_loss_price, 2)),
    )

    try:
        order = trading_client.submit_order(order_request)
        log.info(f"Submitted bracket order: {symbol} qty={qty} "
                 f"SL={stop_loss_price} TP={take_profit_price}")
        return jsonify({"status": "order submitted", "order_id": str(order.id)}), 200
    except Exception as e:
        log.error(f"Error submitting order for {symbol}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/", methods=["GET"])
def health_check():
    return jsonify({"status": "bridge is running", "paper_trading": PAPER_TRADING}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)


# =================================================================
# README -- SETUP INSTRUCTIONS
# =================================================================
#
# 1) INSTALL DEPENDENCIES (run this in a terminal):
#      pip install flask alpaca-py
#
# 2) SET YOUR ENVIRONMENT VARIABLES (replace with your real keys):
#      export ALPACA_API_KEY="your_key_id_here"
#      export ALPACA_API_SECRET="your_secret_key_here"
#      export ALPACA_PAPER="true"
#      export WEBHOOK_SECRET="pick-any-password-you-want"
#
# 3) RUN THE SERVER LOCALLY:
#      python webhook_bridge.py
#    It will start on http://localhost:5000
#
# 4) MAKE IT REACHABLE FROM THE INTERNET (TradingView needs a public URL):
#    - Easiest for testing: install ngrok (ngrok.com), then run:
#        ngrok http 5000
#      This gives you a temporary public URL like https://abcd1234.ngrok.io
#    - For a permanent free option: deploy this file to Render.com's free
#      tier as a "Web Service" -- it gives you a permanent public URL and
#      handles hosting for you (no server management needed).
#
# 5) SET YOUR TRADINGVIEW ALERT WEBHOOK URL to:
#      https://<your-public-url>/webhook
#
# 6) SET YOUR TRADINGVIEW ALERT MESSAGE to JSON matching what this script
#    expects. See the updated Pine Script alert() calls -- they should
#    generate a message like this automatically:
#      {
#        "secret": "pick-any-password-you-want",
#        "ticker": "{{ticker}}",
#        "action": "buy",
#        "qty": 1,
#        "stop_loss": 123.45,
#        "take_profit": 150.00
#      }
