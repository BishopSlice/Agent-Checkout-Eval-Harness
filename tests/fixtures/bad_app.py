"""
Intentionally broken Flask app for negative grader tests.
Failures:
  - Uses request.get_json() instead of request.data (G6b will fail → knowledge_gap)
  - No signature verification (G6a will fail)
  - No idempotency (G8 will fail)
  - No DB write (G7 will fail)
"""
import os
import sqlite3

import stripe
from dotenv import load_dotenv
from flask import Flask, jsonify, request

load_dotenv()
app = Flask(__name__)
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
DATABASE_PATH = os.environ.get("DATABASE_PATH", "orders.db")


@app.route("/create-checkout-session", methods=["POST"])
def create_checkout_session():
    run_id = os.environ.get("RUN_ID", "unknown")
    session = stripe.checkout.Session.create(
        mode="payment",
        line_items=[{
            "price_data": {
                "currency": "usd",
                "unit_amount": 2900,
                "product_data": {"name": "Test Product"},
            },
            "quantity": 1,
        }],
        success_url="http://localhost:5000/success?session_id={CHECKOUT_SESSION_ID}",
        cancel_url="http://localhost:5000/cancel",
        billing_address_collection="required",
        client_reference_id=run_id,
    )
    return jsonify({"session_id": session.id, "url": session.url}), 200


@app.route("/webhook", methods=["POST"])
def webhook():
    # BUG: uses get_json() instead of request.data — signature verification will fail
    body = request.get_json(force=True)
    # BUG: no signature verification at all
    if body and body.get("type") == "checkout.session.completed":
        # BUG: no idempotency, no DB write
        pass
    return jsonify({"status": "ok"}), 200


@app.route("/success")
def success():
    return "Payment successful", 200


@app.route("/cancel")
def cancel():
    return "Payment cancelled", 200


if __name__ == "__main__":
    app.run(debug=True)
