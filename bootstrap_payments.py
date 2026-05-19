#!/usr/bin/env python3
"""
WhalePulse Payments Bootstrap
Run this to add payment/subscription files to your WhalePulse install.
"""
import os

BASE = os.path.expanduser("~/whalepulse")

FILES = {}

# ── shared/payments_db.py — Subscription database ──
FILES["shared/payments_db.py"] = r'''"""
WhalePulse Payments Database
Manages subscriber records, links Stripe customers to Telegram users.
"""
import sqlite3
import time
from pathlib import Path
from contextlib import contextmanager

DB_PATH = Path(__file__).parent.parent / "data" / "whalepulse.db"

@contextmanager
def get_db():
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_payments_db():
    with get_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS subscribers (
            telegram_id INTEGER PRIMARY KEY,
            telegram_username TEXT DEFAULT '',
            stripe_customer_id TEXT DEFAULT '',
            stripe_subscription_id TEXT DEFAULT '',
            plan TEXT DEFAULT 'free',
            status TEXT DEFAULT 'inactive',
            started_at INTEGER DEFAULT 0,
            expires_at INTEGER DEFAULT 0,
            cancelled_at INTEGER DEFAULT 0,
            last_payment INTEGER DEFAULT 0,
            total_paid REAL DEFAULT 0.0,
            in_channel INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_sub_stripe ON subscribers(stripe_customer_id);
        CREATE INDEX IF NOT EXISTS idx_sub_status ON subscribers(status);
        CREATE INDEX IF NOT EXISTS idx_sub_expires ON subscribers(expires_at);
        """)

def upsert_subscriber(telegram_id, **kwargs):
    with get_db() as db:
        existing = db.execute("SELECT * FROM subscribers WHERE telegram_id = ?",
                              (telegram_id,)).fetchone()
        if existing:
            if kwargs:
                sets = ", ".join(f"{k} = ?" for k in kwargs)
                vals = list(kwargs.values()) + [telegram_id]
                db.execute(f"UPDATE subscribers SET {sets} WHERE telegram_id = ?", vals)
        else:
            kwargs["telegram_id"] = telegram_id
            cols = ", ".join(kwargs.keys())
            placeholders = ", ".join("?" for _ in kwargs)
            db.execute(f"INSERT INTO subscribers ({cols}) VALUES ({placeholders})",
                       list(kwargs.values()))

def get_subscriber(telegram_id):
    with get_db() as db:
        return db.execute("SELECT * FROM subscribers WHERE telegram_id = ?",
                          (telegram_id,)).fetchone()

def get_subscriber_by_stripe(stripe_customer_id):
    with get_db() as db:
        return db.execute("SELECT * FROM subscribers WHERE stripe_customer_id = ?",
                          (stripe_customer_id,)).fetchone()

def get_active_subscribers():
    with get_db() as db:
        return db.execute(
            "SELECT * FROM subscribers WHERE status = 'active' ORDER BY started_at DESC"
        ).fetchall()

def get_expired_subscribers():
    now = int(time.time())
    with get_db() as db:
        return db.execute(
            "SELECT * FROM subscribers WHERE status = 'active' AND expires_at > 0 AND expires_at < ?",
            (now,)).fetchall()

def get_subscriber_stats():
    with get_db() as db:
        active = db.execute("SELECT COUNT(*) FROM subscribers WHERE status = 'active'").fetchone()[0]
        total = db.execute("SELECT COUNT(*) FROM subscribers").fetchone()[0]
        revenue = db.execute("SELECT SUM(total_paid) FROM subscribers").fetchone()[0] or 0
        return {"active": active, "total": total, "total_revenue": revenue}
'''

# ── bots/payments/webhook_server.py — Stripe webhook handler ──
FILES["bots/payments/webhook_server.py"] = r'''#!/usr/bin/env python3
"""
WhalePulse Stripe Webhook Server
Listens for Stripe events and manages subscriptions.
Runs on port 4242.
"""
import sys, os, json, time, hmac, hashlib
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / "config" / ".env")

from shared.payments_db import (
    init_payments_db, upsert_subscriber, get_subscriber_by_stripe, get_subscriber
)

import httpx

STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
PRO_CHANNEL_ID = os.getenv("TELEGRAM_PRO_CHANNEL_ID", "")
ADMIN_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

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
        # Create a single-use invite link
        r = httpx.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/createChatInviteLink",
            json={
                "chat_id": PRO_CHANNEL_ID,
                "member_limit": 1,
                "expire_date": int(time.time()) + 86400,  # 24h to use
                "name": f"pro-{telegram_id}"
            }, timeout=15)
        if r.status_code == 200:
            data = r.json()
            link = data["result"]["invite_link"]
            send_telegram(telegram_id,
                "Welcome to WhalePulse Pro!\n\n"
                f"Here's your private channel invite:\n{link}\n\n"
                "This link expires in 24 hours and is single-use.\n"
                "You'll get real-time whale alerts, convergence signals, and AI analysis.")
            return True
        else:
            print(f"  [INVITE ERROR] {r.text[:200]}")
            return False
    except Exception as e:
        print(f"  [INVITE ERROR] {e}")
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
            "To resubscribe, send /subscribe anytime.")

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
    port = int(os.getenv("WEBHOOK_PORT", "4242"))
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
'''

# ── bots/payments/__init__.py ──
FILES["bots/payments/__init__.py"] = ""

# ── bots/payments/subscribe_bot.py — Handles /subscribe command ──
FILES["bots/payments/subscribe_bot.py"] = r'''#!/usr/bin/env python3
"""
WhalePulse Subscription Bot
Handles /subscribe, /account, /cancel commands.
Generates Stripe Checkout links with Telegram user metadata.
"""
import sys, os, time, json
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from dotenv import load_dotenv
import httpx

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / "config" / ".env")

from shared.payments_db import (
    init_payments_db, get_subscriber, upsert_subscriber, get_subscriber_stats
)

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID", "")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ADMIN_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
DOMAIN = os.getenv("DOMAIN", "http://localhost:4242")


def create_checkout_session(telegram_id, telegram_username):
    """Create a Stripe Checkout session with Telegram metadata."""
    if not STRIPE_SECRET_KEY or not STRIPE_PRICE_ID:
        return None

    try:
        r = httpx.post(
            "https://api.stripe.com/v1/checkout/sessions",
            auth=(STRIPE_SECRET_KEY, ""),
            data={
                "mode": "subscription",
                "line_items[0][price]": STRIPE_PRICE_ID,
                "line_items[0][quantity]": "1",
                "success_url": f"{DOMAIN}/success?session_id={{CHECKOUT_SESSION_ID}}",
                "cancel_url": f"{DOMAIN}/cancel",
                "metadata[telegram_id]": str(telegram_id),
                "metadata[telegram_username]": telegram_username or "",
                "subscription_data[metadata][telegram_id]": str(telegram_id),
                "subscription_data[metadata][telegram_username]": telegram_username or "",
                "allow_promotion_codes": "true",
            },
            timeout=15)

        if r.status_code == 200:
            return r.json().get("url")
        else:
            print(f"  [STRIPE ERROR] {r.status_code}: {r.text[:200]}")
            return None
    except Exception as e:
        print(f"  [STRIPE ERROR] {e}")
        return None


def create_billing_portal(stripe_customer_id):
    """Create a Stripe billing portal session for managing subscription."""
    if not STRIPE_SECRET_KEY or not stripe_customer_id:
        return None
    try:
        r = httpx.post(
            "https://api.stripe.com/v1/billing_portal/sessions",
            auth=(STRIPE_SECRET_KEY, ""),
            data={
                "customer": stripe_customer_id,
                "return_url": f"{DOMAIN}/",
            },
            timeout=15)
        if r.status_code == 200:
            return r.json().get("url")
        return None
    except:
        return None


async def cmd_subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /subscribe command."""
    user = update.effective_user
    sub = get_subscriber(user.id)

    if sub and sub["status"] == "active":
        await update.message.reply_text(
            "You're already a WhalePulse Pro member!\n\n"
            "Send /account to manage your subscription.")
        return

    # Create checkout session
    checkout_url = create_checkout_session(user.id, user.username)

    if checkout_url:
        keyboard = [[InlineKeyboardButton("Subscribe — $29/mo", url=checkout_url)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "WhalePulse Pro — $29/month\n\n"
            "What you get:\n"
            "  Real-time whale alerts (instant)\n"
            "  Convergence detection\n"
            "  AI trade analysis\n"
            "  50+ tracked wallets\n"
            "  Daily AI digest\n"
            "  Admin commands\n\n"
            "Click below to subscribe:",
            reply_markup=reply_markup)
    else:
        await update.message.reply_text(
            "Payment system is being set up. Contact @dinadisantini to subscribe manually.\n\n"
            "Price: $29/month for WhalePulse Pro.")

    # Track interest even if they don't pay
    upsert_subscriber(user.id, telegram_username=user.username or "")


async def cmd_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /account command — show subscription status."""
    user = update.effective_user
    sub = get_subscriber(user.id)

    if not sub:
        await update.message.reply_text(
            "No subscription found.\nSend /subscribe to get WhalePulse Pro.")
        return

    status_emoji = {"active": "Active", "past_due": "Payment overdue",
                    "cancelled": "Cancelled", "inactive": "Inactive"}
    status = status_emoji.get(sub["status"], sub["status"])

    msg = (
        f"WhalePulse Account\n\n"
        f"Plan: {sub['plan'].title()}\n"
        f"Status: {status}\n"
        f"Total paid: ${sub['total_paid']:.2f}\n")

    if sub["status"] == "active" and sub["stripe_customer_id"]:
        portal_url = create_billing_portal(sub["stripe_customer_id"])
        if portal_url:
            keyboard = [[InlineKeyboardButton("Manage Subscription", url=portal_url)]]
            await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))
            return

    await update.message.reply_text(msg)


async def cmd_subs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command — show subscriber stats."""
    user = update.effective_user
    if str(user.id) != ADMIN_CHAT_ID:
        return

    stats = get_subscriber_stats()
    from shared.payments_db import get_active_subscribers
    active = get_active_subscribers()

    msg = (f"SUBSCRIBER STATS\n\n"
           f"Active: {stats['active']}\n"
           f"Total signups: {stats['total']}\n"
           f"Revenue: ${stats['total_revenue']:.2f}\n\n")

    if active:
        msg += "Active subscribers:\n"
        for s in active[:20]:
            msg += f"  @{s['telegram_username'] or s['telegram_id']}\n"

    await update.message.reply_text(msg)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome to WhalePulse!\n\n"
        "The smartest Polymarket whale tracker.\n\n"
        "/subscribe — Get Pro ($29/mo)\n"
        "/account — Manage subscription\n"
        "/status — Bot status\n"
        "/whales — Top tracked wallets\n"
        "/trades — Recent whale trades\n"
        "/help — All commands")


def main():
    print("=" * 50)
    print("  WhalePulse Subscription Bot")
    print("=" * 50)

    init_payments_db()
    token = TELEGRAM_TOKEN
    if not token:
        print("ERROR: No TELEGRAM_BOT_TOKEN")
        sys.exit(1)

    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("subscribe", cmd_subscribe))
    app.add_handler(CommandHandler("account", cmd_account))
    app.add_handler(CommandHandler("subs", cmd_subs))
    app.add_handler(CommandHandler("start", cmd_start))

    print("Subscription bot running.")
    app.run_polling()

if __name__ == "__main__":
    main()
'''

# ── bots/payments/subscription_checker.py — Checks for expired subs ──
FILES["bots/payments/subscription_checker.py"] = r'''#!/usr/bin/env python3
"""
WhalePulse Subscription Checker
Runs periodically to remove expired subscribers from the Pro channel.
Can be called by cron or run as a standalone periodic script.
"""
import sys, os, time
from pathlib import Path
from dotenv import load_dotenv
import httpx
import schedule

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / "config" / ".env")

from shared.payments_db import (
    init_payments_db, get_expired_subscribers, upsert_subscriber
)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
PRO_CHANNEL_ID = os.getenv("TELEGRAM_PRO_CHANNEL_ID", "")
ADMIN_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

def send_telegram(chat_id, text):
    if not TELEGRAM_TOKEN: return
    try:
        httpx.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                   json={"chat_id": str(chat_id), "text": text}, timeout=15)
    except: pass

def kick_user(telegram_id):
    if not TELEGRAM_TOKEN or not PRO_CHANNEL_ID: return False
    try:
        r = httpx.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/banChatMember",
                       json={"chat_id": PRO_CHANNEL_ID, "user_id": telegram_id}, timeout=15)
        if r.status_code == 200:
            httpx.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/unbanChatMember",
                       json={"chat_id": PRO_CHANNEL_ID, "user_id": telegram_id,
                             "only_if_banned": True}, timeout=15)
        return r.status_code == 200
    except: return False

def check_expired():
    print(f"[{time.strftime('%H:%M')}] Checking expired subscriptions...")
    expired = get_expired_subscribers()
    removed = 0
    for sub in expired:
        print(f"  Expired: @{sub['telegram_username']} ({sub['telegram_id']})")
        kicked = kick_user(sub["telegram_id"])
        upsert_subscriber(sub["telegram_id"], status="expired", in_channel=0)
        send_telegram(sub["telegram_id"],
            "Your WhalePulse Pro access has expired.\n"
            "Send /subscribe to reactivate.")
        if kicked: removed += 1
    if removed:
        send_telegram(ADMIN_CHAT_ID, f"Removed {removed} expired subscribers")
    print(f"  Checked {len(expired)} expired, removed {removed}")

if __name__ == "__main__":
    init_payments_db()
    print("Subscription checker running. Checking every hour.")
    check_expired()  # Run immediately
    schedule.every(1).hours.do(check_expired)
    while True:
        try:
            schedule.run_pending()
            time.sleep(60)
        except KeyboardInterrupt:
            break
'''

def main():
    print("WhalePulse Payments Bootstrap")
    print("=" * 40)
    written = 0
    for relpath, content in FILES.items():
        fullpath = os.path.join(BASE, relpath)
        os.makedirs(os.path.dirname(fullpath), exist_ok=True)
        with open(fullpath, "w") as f:
            f.write(content)
        written += 1
        print(f"  OK {relpath}")
    print(f"\nWrote {written} files")
    print("\n" + "=" * 50)
    print("NEXT STEPS:")
    print("=" * 50)
    print("""
1. Add these to your ~/whalepulse/config/.env:
   STRIPE_SECRET_KEY=sk_live_xxx     (from Stripe Dashboard > Developers > API keys)
   STRIPE_PRICE_ID=price_xxx         (from the product you created in Stripe)
   STRIPE_WEBHOOK_SECRET=whsec_xxx   (from Stripe > Webhooks after adding endpoint)
   TELEGRAM_PRO_CHANNEL_ID=-100xxx   (your private Pro channel ID)
   DOMAIN=http://YOUR_SERVER_IP:4242

2. Open firewall port:
   sudo ufw allow 4242

3. In Stripe Dashboard > Developers > Webhooks:
   Add endpoint: http://YOUR_SERVER_IP:4242/webhook/stripe
   Select events:
     - checkout.session.completed
     - customer.subscription.updated
     - customer.subscription.deleted
     - invoice.payment_succeeded
     - invoice.payment_failed

4. Test the webhook server:
   cd ~/whalepulse
   PYTHONPATH=. venv/bin/python bots/payments/webhook_server.py

5. Test the subscribe bot:
   PYTHONPATH=. venv/bin/python bots/payments/subscribe_bot.py

6. Create systemd services (commands provided after testing)
""")

if __name__ == "__main__":
    main()
