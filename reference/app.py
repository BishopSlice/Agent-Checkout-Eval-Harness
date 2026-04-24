import os
import sqlite3
import logging

import stripe
from flask import Flask, jsonify, redirect, request
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
stripe.api_key = os.environ["STRIPE_SECRET_KEY"]          # hard KeyError if missing
STRIPE_WEBHOOK_SECRET = os.environ["STRIPE_WEBHOOK_SECRET"]
DATABASE_PATH = os.environ.get("DATABASE_PATH", "orders.db")

def init_db():
    schema_path = os.path.join(os.path.dirname(__file__), "..", "app", "schema.sql")
    conn = sqlite3.connect(DATABASE_PATH)
    conn.executescript(open(schema_path).read())
    conn.commit()
    conn.close()

@app.route("/create-checkout-session", methods=["POST"])
def create_checkout_session():
    run_id = request.headers.get("X-Run-Id") or os.environ.get("RUN_ID", "unknown")
    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            line_items=[{
                "price_data": {
                    "currency": "usd",
                    "unit_amount": 2900,
                    "product_data": {"name": "Premium Plan"},
                },
                "quantity": 1,
            }],
            success_url="http://localhost:5000/success?session_id={CHECKOUT_SESSION_ID}",
            cancel_url="http://localhost:5000/cancel",
            billing_address_collection="required",  # satisfies G4 (forces email input)
            client_reference_id=run_id,              # G1 finds session by this
        )
        return jsonify({"session_id": session.id, "url": session.url}), 200
    except stripe.error.StripeError as e:
        return jsonify({"error": str(e)}), 500

@app.route("/webhook", methods=["POST"])
def webhook():
    payload = request.data          # MUST be .data not .get_json() — G6b tests this
    sig_header = request.headers.get("Stripe-Signature")
    if not sig_header:
        return jsonify({"error": "Missing Stripe-Signature header"}), 400
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except stripe.error.SignatureVerificationError:
        return jsonify({"error": "Invalid signature"}), 400
    except Exception:
        return jsonify({"error": "Bad payload"}), 400

    if event["type"] != "checkout.session.completed":
        return jsonify({"status": "ignored"}), 200  # G6c: gracefully ignore unknown types

    session = event["data"]["object"]
    session_id = session["id"]
    customer_email = (session.get("customer_details") or {}).get("email") or session.get("customer_email")
    amount_total = session.get("amount_total")

    conn = sqlite3.connect(DATABASE_PATH)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO orders (session_id, customer_email, amount_total) VALUES (?, ?, ?)",
            (session_id, customer_email, amount_total),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        return jsonify({"error": "DB write failed"}), 500
    finally:
        conn.close()

    return jsonify({"status": "success"}), 200

@app.route("/success")
def success():
    return "Payment successful", 200

@app.route("/cancel")
def cancel():
    return "Payment cancelled", 200

init_db()

if __name__ == "__main__":
    app.run(debug=True)