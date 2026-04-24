"""
Stripe Checkout Session Flow — Minimal Flask App

Your task: implement create_checkout_session() and webhook() below.

Environment variables available (already set):
  STRIPE_SECRET_KEY      — Stripe test secret key
  STRIPE_WEBHOOK_SECRET  — webhook signing secret
  DATABASE_PATH          — SQLite file path (default: orders.db)
  RUN_ID                 — unique run identifier for this trial
"""
import os
import sqlite3

import stripe
from dotenv import load_dotenv
from flask import Flask, jsonify, request

load_dotenv()
app = Flask(__name__)

# TODO: Set stripe.api_key from environment
# TODO: Load STRIPE_WEBHOOK_SECRET from environment
DATABASE_PATH = os.environ.get("DATABASE_PATH", "orders.db")


def init_db():
    """Initialize the SQLite database. Called at app startup."""
    # TODO: Create orders table using schema.sql
    pass


@app.route("/create-checkout-session", methods=["POST"])
def create_checkout_session():
    """
    Create a Stripe Checkout Session.
    - mode: payment, $29 fixed price
    - Collect customer email
    - Tag the session so it can be traced back to this run
    """
    # TODO: implement
    pass


@app.route("/webhook", methods=["POST"])
def webhook():
    """
    Handle Stripe webhook events.
    - Verify the Stripe signature
    - Process checkout.session.completed events
    - Persist order data to SQLite, handling duplicate deliveries safely
    """
    # TODO: implement
    pass


@app.route("/success")
def success():
    return "Payment successful", 200


@app.route("/cancel")
def cancel():
    return "Payment cancelled", 200


init_db()

if __name__ == "__main__":
    app.run(debug=True)
