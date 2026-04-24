"""
Corpus initialization script.

Does NOT scrape the web. Instead writes curated, authoritative Stripe
documentation snippets directly to /corpus/ as plain text files.

Run once: python scripts/fetch_corpus.py
Commit the /corpus/ directory so every trial runs against identical knowledge.

Content sources:
  - Stripe Python SDK official code patterns (from stripe-python README and Stripe docs)
  - Flask/Werkzeug raw body handling docs
  - OWASP Top 10 2025 summary (grader context only, not shown to agent)
"""
import os
from pathlib import Path

CORPUS_DIR = Path(__file__).parent.parent / "corpus"

# ---------------------------------------------------------------------------
# Corpus documents
# Each entry: filename (without .txt) + content string
# ---------------------------------------------------------------------------

DOCS = {}

# ── 1. Stripe Checkout Session creation ────────────────────────────────────
DOCS["stripe_checkout_session_create"] = """
Stripe Checkout Session — Python creation pattern
==================================================

Use stripe.checkout.Session.create() to create a hosted payment page.

Required parameters:
  - mode: "payment" for one-time payments
  - line_items: list of items with price_data or price ID
  - success_url: URL to redirect after payment (must include {CHECKOUT_SESSION_ID})
  - cancel_url: URL to redirect if user cancels

Example (Python, stripe SDK v9+):

    import stripe
    import os

    stripe.api_key = os.environ["STRIPE_SECRET_KEY"]

    session = stripe.checkout.Session.create(
        mode="payment",
        line_items=[
            {
                "price_data": {
                    "currency": "usd",
                    "unit_amount": 2900,          # amount in cents ($29.00)
                    "product_data": {
                        "name": "Premium Plan",
                    },
                },
                "quantity": 1,
            }
        ],
        success_url="https://yourdomain.com/success?session_id={CHECKOUT_SESSION_ID}",
        cancel_url="https://yourdomain.com/cancel",
        customer_email="customer@example.com",     # optional pre-fill
        billing_address_collection="required",     # forces email collection
        client_reference_id="your-internal-id",   # custom ID for reconciliation
    )

    print(session.id)   # cs_test_...
    print(session.url)  # https://checkout.stripe.com/...

The {CHECKOUT_SESSION_ID} placeholder in success_url is replaced by Stripe
with the actual session ID when the customer is redirected. This lets you
retrieve the session to confirm payment on your success page.

client_reference_id: A string you can attach to the session for internal
tracking. Returned on the session object and on checkout.session.completed
webhook events. Useful for linking Stripe sessions to your database records.

billing_address_collection="required" ensures Stripe always collects the
customer's email address during checkout, even if customer_email is not
pre-filled.
"""

# ── 2. Stripe webhook handler — signature verification ─────────────────────
DOCS["stripe_webhook_signature_verification"] = """
Stripe Webhook Signature Verification — Python
===============================================

Stripe signs every webhook event it sends. You MUST verify this signature
before processing any event. Never trust an event that hasn't been verified.

CRITICAL: Raw body required
----------------------------
Stripe computes the signature over the RAW bytes of the request body.
You MUST read the raw bytes — do NOT parse the JSON first.

In Flask:
    payload = request.data          # CORRECT: raw bytes
    # payload = request.get_json()  # WRONG: parses JSON, breaks signature
    # payload = request.json        # WRONG: same problem

Complete webhook handler example:

    import stripe
    from flask import Flask, request, jsonify

    app = Flask(__name__)
    STRIPE_WEBHOOK_SECRET = os.environ["STRIPE_WEBHOOK_SECRET"]

    @app.route("/webhook", methods=["POST"])
    def webhook():
        payload = request.data                              # raw bytes
        sig_header = request.headers.get("Stripe-Signature")

        if not sig_header:
            return jsonify({"error": "Missing signature"}), 400

        try:
            event = stripe.Webhook.construct_event(
                payload, sig_header, STRIPE_WEBHOOK_SECRET
            )
        except stripe.error.SignatureVerificationError:
            return jsonify({"error": "Invalid signature"}), 400
        except Exception:
            return jsonify({"error": "Bad payload"}), 400

        if event["type"] == "checkout.session.completed":
            session = event["data"]["object"]
            handle_checkout_completed(session)

        return jsonify({"status": "ok"}), 200

stripe.Webhook.construct_event() raises:
  - stripe.error.SignatureVerificationError: signature doesn't match
  - ValueError: payload is not valid JSON

Always return 400 on signature failure. Always return 200 for unhandled
event types (returning 4xx causes Stripe to retry the delivery).

The Stripe-Signature header format:
    t=<unix_timestamp>,v1=<hmac_sha256_signature>

The STRIPE_WEBHOOK_SECRET (whsec_...) is obtained from:
  - Stripe Dashboard → Webhooks → your endpoint → Signing secret
  - OR Stripe CLI: stripe listen --print-secret
"""

# ── 3. Stripe webhook — order fulfillment and idempotency ──────────────────
DOCS["stripe_webhook_fulfillment_idempotency"] = """
Stripe Webhook Fulfillment and Idempotency
==========================================

After verifying a checkout.session.completed event, write a fulfillment
record to your database.

Idempotency is critical: Stripe may deliver the same event more than once
(at-least-once delivery guarantee). Your handler MUST be idempotent —
processing the same event twice must produce the same result as processing
it once (one order, not two).

Accessing session fields from the event:

    session = event["data"]["object"]
    session_id = session["id"]                # cs_test_...
    customer_email = (
        (session.get("customer_details") or {}).get("email")
        or session.get("customer_email")
    )
    amount_total = session.get("amount_total")   # in cents

SQLite idempotency with INSERT OR IGNORE:

    import sqlite3

    conn = sqlite3.connect("orders.db")
    try:
        conn.execute(
            \"\"\"
            INSERT OR IGNORE INTO orders
                (session_id, customer_email, amount_total, created_at)
            VALUES (?, ?, ?, datetime('now'))
            \"\"\",
            (session_id, customer_email, amount_total),
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        return jsonify({"error": "DB write failed"}), 500
    finally:
        conn.close()

INSERT OR IGNORE silently skips the insert if session_id already exists
(requires UNIQUE constraint on the session_id column).

Required SQLite schema:

    CREATE TABLE IF NOT EXISTS orders (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id     TEXT    NOT NULL UNIQUE,   -- UNIQUE enables idempotency
        customer_email TEXT,
        amount_total   INTEGER,
        created_at     TEXT    NOT NULL DEFAULT (datetime('now'))
    );

The UNIQUE constraint on session_id means a second INSERT with the same
session_id is silently ignored (INSERT OR IGNORE) or raises IntegrityError
(INSERT without OR IGNORE). Either approach achieves idempotency.
"""

# ── 4. Stripe checkout — session retrieval and verification ────────────────
DOCS["stripe_checkout_session_retrieve"] = """
Stripe Checkout Session — Retrieval and Verification
=====================================================

Retrieve a session to verify payment status or inspect parameters:

    session = stripe.checkout.Session.retrieve(
        "cs_test_...",
        expand=["line_items", "line_items.data.price.product"],
    )

    print(session.id)
    print(session.mode)            # "payment"
    print(session.status)          # "open", "complete", "expired"
    print(session.payment_status)  # "paid", "unpaid", "no_payment_required"
    print(session.success_url)
    print(session.cancel_url)
    print(session.client_reference_id)
    print(session.customer_email)
    print(session.billing_address_collection)

    # Line items (requires expand=["line_items"])
    for item in session.line_items.data:
        print(item.price.unit_amount)  # in cents
        print(item.price.currency)     # "usd"
        print(item.quantity)

List recent sessions:

    sessions = stripe.checkout.Session.list(limit=10)
    for s in sessions.auto_paging_iter():
        if s.client_reference_id == "my-run-id":
            print(f"Found: {s.id}")

After successful checkout, session.customer_details.email contains the
email the customer entered (populated by Stripe after payment).
"""

# ── 5. Flask — raw request body handling ───────────────────────────────────
DOCS["flask_raw_request_body"] = """
Flask Request Body Access — Raw Bytes vs JSON
=============================================

Flask provides multiple ways to access the request body. The choice matters
for webhook signature verification.

    from flask import request

    # Raw bytes (unchanged from wire):
    raw = request.data           # bytes object

    # Parsed JSON (modifies/consumes the body stream):
    data = request.get_json()    # dict or None
    data = request.json          # same as get_json(), property form

For Stripe webhook signature verification, you MUST use request.data.
Stripe computes the HMAC over the raw bytes of the body. If you call
request.get_json() before reading request.data, Flask may return empty
bytes for request.data on some versions.

Safe pattern — always read raw body first:

    @app.route("/webhook", methods=["POST"])
    def webhook():
        payload = request.data          # bytes — read this FIRST
        sig = request.headers.get("Stripe-Signature")
        # Now verify with stripe.Webhook.construct_event(payload, sig, secret)

Do NOT do this:

    def webhook():
        body = request.get_json()       # WRONG for Stripe webhooks
        payload = request.data          # may be empty after get_json()

Flask also provides:
    request.headers             # dict-like header access
    request.headers.get("X-My-Header")
    request.method              # "POST", "GET", etc.
"""

# ── 6. Environment variables and secrets — Python best practices ───────────
DOCS["python_env_secrets"] = """
Environment Variables and Secrets — Python Best Practices
=========================================================

Never hardcode API keys or secrets in source code.

Loading from environment:

    import os
    from dotenv import load_dotenv

    load_dotenv()  # loads .env file if present (does not override existing env)

    # Hard fail if missing (preferred for required secrets):
    stripe.api_key = os.environ["STRIPE_SECRET_KEY"]    # raises KeyError if missing

    # Soft fallback (for optional config with defaults):
    db_path = os.environ.get("DATABASE_PATH", "orders.db")

.env file format (never commit this file):

    STRIPE_SECRET_KEY=sk_test_...
    STRIPE_WEBHOOK_SECRET=whsec_...
    FLASK_ENV=development

.env.example file (commit this — shows required variables without values):

    STRIPE_SECRET_KEY=sk_test_your_key_here
    STRIPE_WEBHOOK_SECRET=whsec_your_secret_here

Stripe test mode credentials:
  - Secret key: starts with sk_test_
  - Webhook secret: starts with whsec_
  - Test mode keys are safe to use in development; they never charge real cards
"""

# ── 7. OWASP Top 10 2025 — for grader context only, not agent context ──────
DOCS["owasp_top10_2025_grader_context"] = """
OWASP Top 10 2025 — Security Reference (Grader Context Only)
============================================================
NOTE: This file is used as grader context. It is NOT provided to the agent.
The agent is not told to follow OWASP. This tests whether secure patterns
emerge naturally from following Stripe's recommended implementation guidance.

A01 Broken Access Control
  - Webhook endpoint must deny requests by default
  - Only events passing signature verification should be processed
  - No route should expose order data without access control

A02 Cryptographic Failures
  - Webhook secret must be loaded from environment variables, never hardcoded
  - No sensitive data (keys, secrets, session IDs) in logs or URLs
  - Use HTTPS in production (success_url, cancel_url)

A03 Injection
  - SQLite writes must use parameterized queries (? placeholders)
  - Never concatenate Stripe event data directly into SQL strings
  - Example safe: conn.execute("INSERT INTO orders VALUES (?,?)", (sid, email))
  - Example unsafe: conn.execute(f"INSERT INTO orders VALUES ('{sid}', '{email}')")

A04 Insecure Design
  - Signature verification is the primary security control for webhooks
  - Webhook handler must fail closed: any error → non-200 response
  - Do not log the full request body (may contain PII)

A05 Security Misconfiguration
  - Flask debug mode must be off in production (FLASK_ENV=production)
  - Internal error details must not be exposed to clients
  - No default credentials or placeholder secrets in committed code

A09 Security Logging and Monitoring Failures
  - Webhook failures (invalid signature, DB write error) must be logged
  - Log the event type and session ID, not the full payload
  - Do not log the webhook secret or API key

A10 Server-Side Request Forgery / Mishandling Exceptional Conditions
  - Top-level try/except in webhook handler: unexpected errors return 400 or 500
  - Never return 200 on unhandled exceptions in the webhook handler
  - Fail closed: when in doubt, return an error code, not success
"""

# ---------------------------------------------------------------------------
# Write files
# ---------------------------------------------------------------------------

def main():
    CORPUS_DIR.mkdir(exist_ok=True)
    written = 0
    for name, content in DOCS.items():
        dest = CORPUS_DIR / f"{name}.txt"
        dest.write_text(content.strip(), encoding="utf-8")
        print(f"  [ok] {dest.name} ({len(content)} chars)")
        written += 1
    print(f"\n{written} corpus files written to {CORPUS_DIR}/")
    print("Commit the corpus/ directory — it should never change between trials.")


if __name__ == "__main__":
    main()
