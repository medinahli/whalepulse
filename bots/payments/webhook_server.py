#!/usr/bin/env python3
"""
WhalePulse Stripe Webhook Server
Listens for Stripe events and manages subscriptions.
Runs on port 80.
"""
import sys, os, json, time, hmac, hashlib
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / "config" / ".env")

from shared.payments_db import (
    init_payments_db, upsert_subscriber, get_subscriber_by_stripe, get_subscriber,
    complete_referral
)

import httpx

STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
PRO_CHANNEL_ID = os.getenv("TELEGRAM_PRO_CHANNEL_ID", "")
ADMIN_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "PulseWhale_bot")
SUBSCRIBE_LINK = f"https://t.me/{BOT_USERNAME}?start=subscribe"

def send_telegram(chat_id, text):
    if not TELEGRAM_TOKEN or not chat_id:
        return False
    try:
        r = httpx.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": str(chat_id), "text": text, "parse_mode": "HTML"},
            timeout=15)
        return r.status_code == 200
    except:
        return False

def invite_to_channel(telegram_id):
    """Create an invite link and send it to the user."""
    if not TELEGRAM_TOKEN or not PRO_CHANNEL_ID:
        print(f"  [NO CHANNEL CONFIG] Can't invite {telegram_id}")
        return False
    try:
        r = httpx.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/createChatInviteLink",
            json={
                "chat_id": PRO_CHANNEL_ID,
                "member_limit": 1,
                "expire_date": int(time.time()) + 86400,
                "name": f"pro-{telegram_id}"
            }, timeout=15)
        if r.status_code == 200:
            link = r.json()["result"]["invite_link"]
            send_telegram(telegram_id,
                "Welcome to WhalePulse Pro!\n\n"
                f"Your private channel invite (expires 24h, single-use):\n{link}\n\n"
                "What you'll receive:\n"
                "⚡ Real-time trade alerts — whale buys/sells as they happen\n"
                "🐋🐋 Convergence alerts — when 2+ whales bet the same direction\n"
                "✅ Resolution posts — when a called market resolves (was the whale right?)\n"
                "📊 Weekly digest every Monday with top calls and accuracy stats\n"
                "🐋 Whale of the Week every Sunday\n\n"
                "Signal scores are 0–100. Scores ≥70 include a breakdown explaining why.")
            return True
        else:
            print(f"  [INVITE ERROR] {r.text[:200]}")
            return False
    except Exception as e:
        print(f"  [INVITE ERROR] {e}")
        return False


def apply_referrer_credit(stripe_customer_id, amount_cents=2900):
    """Apply a $29 balance credit to the referrer's Stripe account (auto-applies to next invoice)."""
    if not STRIPE_SECRET_KEY or not stripe_customer_id:
        return False
    try:
        r = httpx.post(
            f"https://api.stripe.com/v1/customers/{stripe_customer_id}/balance_transactions",
            auth=(STRIPE_SECRET_KEY, ""),
            data={
                "amount": str(-amount_cents),  # negative = credit
                "currency": "usd",
                "description": "WhalePulse referral reward - 1 month free",
            }, timeout=15)
        return r.status_code == 200
    except Exception as e:
        print(f"  [STRIPE CREDIT ERROR] {e}")
        return False

def kick_from_channel(telegram_id):
    """Remove user from the Pro channel."""
    if not TELEGRAM_TOKEN or not PRO_CHANNEL_ID:
        return False
    try:
        r = httpx.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/banChatMember",
            json={"chat_id": PRO_CHANNEL_ID, "user_id": telegram_id},
            timeout=15)
        # Immediately unban so they can rejoin if they resubscribe
        if r.status_code == 200:
            httpx.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/unbanChatMember",
                json={"chat_id": PRO_CHANNEL_ID, "user_id": telegram_id,
                      "only_if_banned": True}, timeout=15)
        return r.status_code == 200
    except:
        return False

def verify_stripe_signature(payload, sig_header, secret):
    """Verify Stripe webhook signature."""
    if not secret:
        return True  # Skip verification if no secret configured (dev mode)
    try:
        elements = dict(item.split("=", 1) for item in sig_header.split(","))
        timestamp = elements.get("t", "")
        signature = elements.get("v1", "")
        signed_payload = f"{timestamp}.{payload}"
        expected = hmac.new(
            secret.encode(), signed_payload.encode(), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(signature, expected)
    except:
        return False


class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/webhook/stripe":
            self.handle_stripe_webhook()
        elif self.path == "/health":
            self.send_json(200, {"status": "ok"})
        else:
            self.send_json(404, {"error": "not found"})

    def do_GET(self):
        if self.path == "/health":
            self.send_json(200, {"status": "ok", "service": "whalepulse-payments"})
        else:
            self.send_json(200, {"status": "WhalePulse payment server running"})

    def handle_stripe_webhook(self):
        content_length = int(self.headers.get("Content-Length", 0))
        payload = self.rfile.read(content_length).decode("utf-8")
        sig = self.headers.get("Stripe-Signature", "")

        if STRIPE_WEBHOOK_SECRET and not verify_stripe_signature(payload, sig, STRIPE_WEBHOOK_SECRET):
            print("[WEBHOOK] Invalid signature")
            self.send_json(400, {"error": "invalid signature"})
            return

        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            self.send_json(400, {"error": "invalid json"})
            return

        event_type = event.get("type", "")
        data = event.get("data", {}).get("object", {})

        print(f"[WEBHOOK] {event_type}")

        if event_type == "checkout.session.completed":
            self.handle_checkout_completed(data)
        elif event_type == "customer.subscription.updated":
            self.handle_subscription_updated(data)
        elif event_type == "customer.subscription.deleted":
            self.handle_subscription_deleted(data)
        elif event_type == "invoice.payment_succeeded":
            self.handle_payment_succeeded(data)
        elif event_type == "invoice.payment_failed":
            self.handle_payment_failed(data)

        self.send_json(200, {"received": True})

    def handle_checkout_completed(self, session):
        """New subscription checkout completed."""
        customer_id = session.get("customer", "")
        sub_id = session.get("subscription", "")
        metadata = session.get("metadata", {})
        telegram_id = metadata.get("telegram_id", "")
        telegram_username = metadata.get("telegram_username", "")

        if not telegram_id:
            print(f"  [WARN] No telegram_id in checkout metadata")
            send_telegram(ADMIN_CHAT_ID,
                f"Payment received but no Telegram ID in metadata!\n"
                f"Customer: {customer_id}\nSubscription: {sub_id}")
            return

        telegram_id = int(telegram_id)
        print(f"  New subscriber: {telegram_username} ({telegram_id})")

        # Save to DB
        upsert_subscriber(telegram_id,
            telegram_username=telegram_username,
            stripe_customer_id=customer_id,
            stripe_subscription_id=sub_id,
            plan="pro",
            status="active",
            started_at=int(time.time()),
            expires_at=int(time.time()) + (35 * 86400),  # 35 days buffer
            last_payment=int(time.time()),
            in_channel=1)

        # Invite to Pro channel
        invited = invite_to_channel(telegram_id)

        # Complete referral if applicable
        referral_code = metadata.get("referral_code", "")
        if referral_code:
            referrer_id = complete_referral(telegram_id)
            if referrer_id:
                referrer_sub = get_subscriber(referrer_id)
                referrer_stripe_id = referrer_sub.get("stripe_customer_id", "") if referrer_sub else ""
                credit_applied = apply_referrer_credit(referrer_stripe_id)
                send_telegram(referrer_id,
                    "🎉 Your referral subscribed to WhalePulse Pro!\n"
                    + ("A $29 credit has been applied to your next invoice — enjoy 1 month free."
                       if credit_applied else
                       "You've earned 1 month free. Contact support to claim your reward."))
                print(f"  Referral completed: referrer={referrer_id}, credit_applied={credit_applied}")

        # Notify admin
        send_telegram(ADMIN_CHAT_ID,
            f"New Pro subscriber!\n"
            f"User: @{telegram_username} ({telegram_id})\n"
            f"Invited to channel: {'Yes' if invited else 'FAILED - invite manually'}")

    def handle_subscription_updated(self, subscription):
        """Subscription status changed."""
        customer_id = subscription.get("customer", "")
        status = subscription.get("status", "")
        sub = get_subscriber_by_stripe(customer_id)

        if not sub:
            return

        if status == "active":
            upsert_subscriber(sub["telegram_id"], status="active",
                              expires_at=int(time.time()) + (35 * 86400))
        elif status in ("past_due", "unpaid"):
            upsert_subscriber(sub["telegram_id"], status="past_due")
            send_telegram(sub["telegram_id"],
                "Your WhalePulse Pro payment is overdue. "
                "Please update your payment method to keep receiving alerts.")
        elif status == "canceled":
            self.handle_subscription_deleted(subscription)

    def handle_subscription_deleted(self, subscription):
        """Subscription cancelled or expired."""
        customer_id = subscription.get("customer", "")
        sub = get_subscriber_by_stripe(customer_id)

        if not sub:
            return

        print(f"  Subscription cancelled: {sub['telegram_username']} ({sub['telegram_id']})")

        upsert_subscriber(sub["telegram_id"],
            status="cancelled",
            cancelled_at=int(time.time()),
            in_channel=0)

        # Remove from channel
        kicked = kick_from_channel(sub["telegram_id"])

        # Notify user
        send_telegram(sub["telegram_id"],
            "Your WhalePulse Pro subscription has ended.\n\n"
            "You'll still get delayed alerts on the free channel.\n"
            f"Resubscribe anytime: {SUBSCRIBE_LINK}")

        # Notify admin
        send_telegram(ADMIN_CHAT_ID,
            f"Subscriber cancelled: @{sub['telegram_username']}\n"
            f"Removed from channel: {'Yes' if kicked else 'Manual removal needed'}")

    def handle_payment_succeeded(self, invoice):
        """Recurring payment succeeded."""
        customer_id = invoice.get("customer", "")
        amount = invoice.get("amount_paid", 0) / 100  # cents to dollars
        sub = get_subscriber_by_stripe(customer_id)

        if sub:
            upsert_subscriber(sub["telegram_id"],
                status="active",
                last_payment=int(time.time()),
                expires_at=int(time.time()) + (35 * 86400),
                total_paid=sub["total_paid"] + amount)
            print(f"  Payment: ${amount} from {sub['telegram_username']}")

    def handle_payment_failed(self, invoice):
        """Payment failed."""
        customer_id = invoice.get("customer", "")
        sub = get_subscriber_by_stripe(customer_id)

        if sub:
            send_telegram(sub["telegram_id"],
                "Your WhalePulse Pro payment failed.\n"
                "Please update your payment method to keep your access.\n\n"
                "Your access will be removed in 3 days if payment isn't resolved.")
            send_telegram(ADMIN_CHAT_ID,
                f"Payment failed: @{sub['telegram_username']}")

    def send_json(self, code, data):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def log_message(self, format, *args):
        print(f"  [HTTP] {args[0]}")


def main():
    init_payments_db()
    port = int(os.getenv("WEBHOOK_PORT", "80"))
    server = HTTPServer(("0.0.0.0", port), WebhookHandler)
    print(f"WhalePulse Payment Server running on port {port}")
    print(f"Webhook URL: http://YOUR_IP:{port}/webhook/stripe")
    send_telegram(ADMIN_CHAT_ID, f"Payment server started on port {port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nPayment server stopped.")
        server.server_close()

if __name__ == "__main__":
    main()
