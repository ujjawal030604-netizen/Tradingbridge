"""
TradingView -> OANDA Webhook Bridge
--------------------------------------
Same idea as your Alpaca bridge, but for OANDA (forex/CFDs).
Listens for TradingView alerts and places matching market orders with
attached take-profit and stop-loss on your OANDA account.

IMPORTANT DIFFERENCES FROM ALPACA:
- OANDA trades in "units" of currency, not "shares". Positive units = buy,
  negative units = sell. E.g. units=1000 buys 1000 units of the base
  currency; units=-1000 sells/shorts 1000 units.
- Instruments use underscores, e.g. "EUR_USD", not "EURUSD".
- There is no separate "paper" account -- you simply use your Practice
  account's token/ID instead of a Live account's, forever, until you
  choose to switch.
"""

import os
import logging
from flask import Flask, request, jsonify
import oandapyV20
import oandapyV20.endpoints.orders as orders
from oandapyV20.contrib.requests import MarketOrderRequest, TakeProfitDetails, StopLossDetails
from oandapyV20.exceptions import V20Error

# ---------------------------------------------------------------
# CONFIG -- from environment variables, never hard-coded.
# ---------------------------------------------------------------
OANDA_API_TOKEN = os.environ.get("OANDA_API_TOKEN")
OANDA_ACCOUNT_ID = os.environ.get("OANDA_ACCOUNT_ID")
OANDA_PRACTICE = os.environ.get("OANDA_PRACTICE", "true").lower() == "true"
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "changeme123")

if not OANDA_API_TOKEN or not OANDA_ACCOUNT_ID:
    raise RuntimeError(
        "Missing OANDA_API_TOKEN or OANDA_ACCOUNT_ID environment variables. "
        "Set these before running the server."
    )

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("oanda_bridge")

app = Flask(__name__)

# "practice" for demo/paper trading, "live" for real money
environment = "practice" if OANDA_PRACTICE else "live"
api = oandapyV20.API(access_token=OANDA_API_TOKEN, environment=environment)


@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(force=True, silent=True)

    if data is None:
        log.warning("Received a non-JSON or empty request body.")
        return jsonify({"error": "invalid or missing JSON body"}), 400

    if data.get("secret") != WEBHOOK_SECRET:
        log.warning("Rejected webhook: bad or missing secret.")
        return jsonify({"error": "unauthorized"}), 401

    try:
        instrument = data["instrument"]          # e.g. "EUR_USD"
        action = data["action"].lower()           # "buy" or "sell"
        units = int(data.get("units", 1000))      # size, e.g. 1000
        stop_loss_price = str(data["stop_loss"])
        take_profit_price = str(data["take_profit"])
    except (KeyError, ValueError, TypeError) as e:
        log.error(f"Malformed alert payload: {data} -- {e}")
        return jsonify({"error": f"missing or bad field: {e}"}), 400

    # OANDA uses positive units for buy, negative units for sell/close-and-short
    order_units = units if action == "buy" else -units

    mkt_order = MarketOrderRequest(
        instrument=instrument,
        units=order_units,
        takeProfitOnFill=TakeProfitDetails(price=take_profit_price).data,
        stopLossOnFill=StopLossDetails(price=stop_loss_price).data,
    )

    try:
        r = orders.OrderCreate(OANDA_ACCOUNT_ID, data=mkt_order.data)
        response = api.request(r)
        log.info(f"Submitted OANDA order: {instrument} units={order_units} "
                 f"SL={stop_loss_price} TP={take_profit_price}")
        return jsonify({"status": "order submitted", "response": response}), 200
    except V20Error as e:
        log.error(f"OANDA API error for {instrument}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/", methods=["GET"])
def health_check():
    return jsonify({"status": "oanda bridge is running", "environment": environment}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)


# =================================================================
# README -- SETUP INSTRUCTIONS
# =================================================================
#
# 1) INSTALL DEPENDENCIES:
#      pip install flask oandapyV20
#
# 2) SET ENVIRONMENT VARIABLES (on Render, or locally for testing):
#      OANDA_API_TOKEN   = your personal access token from OANDA
#      OANDA_ACCOUNT_ID  = your account ID, e.g. 101-001-12345678-001
#      OANDA_PRACTICE    = true   (use your Practice/demo account)
#      WEBHOOK_SECRET    = pick any password
#
# 3) DEPLOY TO RENDER (separate Web Service from your Alpaca bridge):
#      - Upload this file + a requirements.txt containing:
#          flask
#          oandapyV20
#          gunicorn
#      - Start Command: gunicorn oanda_webhook_bridge:app
#
# 4) YOUR TRADINGVIEW ALERT MESSAGE SHOULD SEND JSON LIKE:
#      {
#        "secret": "pick-any-password-you-want",
#        "instrument": "EUR_USD",
#        "action": "buy",
#        "units": 1000,
#        "stop_loss": 1.0700,
#        "take_profit": 1.1000
#      }
#
# Note: for forex, you'd need a SEPARATE Pine Script strategy built for
# currency pairs, not your stock RSI script re-used as-is. Forex moves
# in much smaller price increments and often uses "pips" for sizing
# stop loss/take profit distances rather than percentages.
