#!/usr/bin/env python3
"""
WhalePulse Subscription Bot
Handles /subscribe, /account, /cancel, /trial, /refer, /watch, /feedback commands.
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
    init_payments_db, get_subscriber, upsert_subscriber, get_subscriber_stats,
    get_active_subscribers, add_referral, get_referral_count,
    get_completed_referral_count, claim_trial,
)
from shared.database import (
    init_db, get_missed_paid_alerts,
    add_watchlist, remove_watchlist, get_user_watchlist,
    get_top_wallets, get_stats, get_db, get_alert_history,
)
from shared.ai_client import get_cost_summary

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID", "")
STRIPE_ANNUAL_PRICE_ID = os.getenv("STRIPE_ANNUAL_PRICE_ID", "")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ADMIN_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "PulseWhale_bot")
SUBSCRIBE_LINK = f"https://t.me/{BOT_USERNAME}?start=subscribe"
PRO_CHANNEL_ID = os.getenv("TELEGRAM_PRO_CHANNEL_ID", "")
DOMAIN = os.getenv("DOMAIN", "http://localhost:4242")
TRIAL_DAYS = 3


# ── Stripe helpers ────────────────────────────────────────────────────────────

def _get_or_create_referral_coupon():
    """Return the WHALEPULSE20 coupon ID (20% off first invoice), creating it if needed."""
    if not STRIPE_SECRET_KEY:
        return None
    try:
        r = httpx.get("https://api.stripe.com/v1/coupons/WHALEPULSE20",
                      auth=(STRIPE_SECRET_KEY, ""), timeout=10)
        if r.status_code == 200:
            return "WHALEPULSE20"
        r = httpx.post("https://api.stripe.com/v1/coupons",
                       auth=(STRIPE_SECRET_KEY, ""),
                       data={"id": "WHALEPULSE20", "percent_off": "20",
                             "duration": "once", "name": "Referral - 20% off first month"},
                       timeout=10)
        return "WHALEPULSE20" if r.status_code == 200 else None
    except Exception as e:
        print(f"  [STRIPE] Coupon error: {e}")
        return None


def create_checkout_session(telegram_id, telegram_username, price_id=None, referral_code=""):
    if not STRIPE_SECRET_KEY or not (price_id or STRIPE_PRICE_ID):
        return None
    try:
        # Check if this user was referred — apply 20% discount if so
        sub = get_subscriber(telegram_id)
        referred_by = sub.get("referred_by", 0) if sub else 0
        coupon_id = _get_or_create_referral_coupon() if referred_by else None

        data = {
            "mode": "subscription",
            "line_items[0][price]": price_id or STRIPE_PRICE_ID,
            "line_items[0][quantity]": "1",
            "success_url": f"{DOMAIN}/success?session_id={{CHECKOUT_SESSION_ID}}",
            "cancel_url": f"{DOMAIN}/cancel",
            "metadata[telegram_id]": str(telegram_id),
            "metadata[telegram_username]": telegram_username or "",
            "metadata[referral_code]": referral_code,
            "subscription_data[metadata][telegram_id]": str(telegram_id),
            "subscription_data[metadata][telegram_username]": telegram_username or "",
        }
        if coupon_id:
            data["discounts[0][coupon]"] = coupon_id
        else:
            data["allow_promotion_codes"] = "true"

        r = httpx.post("https://api.stripe.com/v1/checkout/sessions",
                       auth=(STRIPE_SECRET_KEY, ""), data=data, timeout=15)
        if r.status_code == 200:
            return r.json().get("url")
        print(f"  [STRIPE ERROR] {r.status_code}: {r.text[:200]}")
        return None
    except Exception as e:
        print(f"  [STRIPE ERROR] {e}")
        return None


def create_billing_portal(stripe_customer_id):
    if not STRIPE_SECRET_KEY or not stripe_customer_id:
        return None
    try:
        r = httpx.post(
            "https://api.stripe.com/v1/billing_portal/sessions",
            auth=(STRIPE_SECRET_KEY, ""),
            data={"customer": stripe_customer_id, "return_url": f"{DOMAIN}/"},
            timeout=15)
        return r.json().get("url") if r.status_code == 200 else None
    except Exception:
        return None


def create_pro_invite(telegram_id, expire_days=1):
    """Create a single-use Pro channel invite link."""
    if not TELEGRAM_TOKEN or not PRO_CHANNEL_ID:
        return None
    try:
        r = httpx.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/createChatInviteLink",
            json={
                "chat_id": PRO_CHANNEL_ID,
                "member_limit": 1,
                "expire_date": int(time.time()) + (expire_days * 86400),
                "name": f"trial-{telegram_id}",
            }, timeout=15)
        if r.status_code == 200:
            return r.json()["result"]["invite_link"]
        return None
    except Exception:
        return None


def kick_from_channel(telegram_id):
    if not TELEGRAM_TOKEN or not PRO_CHANNEL_ID:
        return False
    try:
        r = httpx.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/banChatMember",
            json={"chat_id": PRO_CHANNEL_ID, "user_id": telegram_id}, timeout=15)
        if r.status_code == 200:
            httpx.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/unbanChatMember",
                json={"chat_id": PRO_CHANNEL_ID, "user_id": telegram_id,
                      "only_if_banned": True}, timeout=15)
        return r.status_code == 200
    except Exception:
        return False


# ── Commands ──────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    sub = get_subscriber(user.id)

    # Handle deep links: /start subscribe or /start ref_wp123456
    if context.args and context.args[0] == "subscribe":
        await cmd_subscribe(update, context)
        return

    if context.args:
        ref_arg = context.args[0]
        if ref_arg.startswith("ref_wp"):
            try:
                referrer_id = int(ref_arg[6:])
                if referrer_id != user.id:
                    upsert_subscriber(user.id, telegram_username=user.username or "",
                                      referred_by=referrer_id)
                    add_referral(referrer_id, user.id)
            except ValueError:
                pass

    # Determine if user is eligible for trial
    trial_eligible = (not sub or sub["status"] == "inactive") and \
                     (not sub or not sub.get("used_trial", 0))

    msg = (
        "Welcome to WhalePulse!\n\n"
        "The smartest Polymarket whale tracker.\n\n"
        f"<a href='{SUBSCRIBE_LINK}'>Get Pro ($29/mo)</a> — real-time whale alerts\n"
        "/account — Manage subscription\n"
        "/watch [keyword] — Get DMs for markets you care about\n"
        "/help — All commands\n")
    if trial_eligible:
        msg += "\n<b>New?</b> Try Pro free for 3 days — /trial"
    await update.message.reply_html(msg)


async def cmd_subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    sub = get_subscriber(user.id)

    if sub and sub["status"] in ("active", "trial"):
        await update.message.reply_text(
            "You're already a WhalePulse Pro member!\n\nSend /account to manage your subscription.")
        return

    # Get subscriber count for social proof
    try:
        stats = get_subscriber_stats()
        member_count = stats.get("active", 0)
        social_proof = f"\n👥 {member_count} active Pro members" if member_count > 0 else ""
    except Exception:
        social_proof = ""

    # Build keyboard with monthly + optional annual
    referral_code = f"wp{user.id}"
    monthly_url = create_checkout_session(user.id, user.username,
                                          price_id=STRIPE_PRICE_ID,
                                          referral_code=referral_code)
    annual_url = create_checkout_session(user.id, user.username,
                                         price_id=STRIPE_ANNUAL_PRICE_ID,
                                         referral_code=referral_code) if STRIPE_ANNUAL_PRICE_ID else None

    upsert_subscriber(user.id, telegram_username=user.username or "")

    if monthly_url:
        keyboard = [[InlineKeyboardButton("Monthly — $29/mo", url=monthly_url)]]
        if annual_url:
            keyboard.append([InlineKeyboardButton("Annual — $249/yr (save 28%)", url=annual_url)])
        await update.message.reply_html(
            f"<b>WhalePulse Pro</b>{social_proof}\n\n"
            "What you get:\n"
            "  ⚡ Real-time whale alerts (instant)\n"
            "  🐋 Convergence detection\n"
            "  🤖 AI trade analysis\n"
            "  50+ tracked wallets\n"
            "  📊 Weekly digests\n"
            "  👀 Market watchlist (DMs)\n\n"
            "Choose your plan:",
            reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(
            "Payment system is being set up. Contact admin to subscribe.\n\nPrice: $29/month.")


async def cmd_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    sub = get_subscriber(user.id)

    if not sub or sub["status"] == "inactive":
        # Show what they're missing as a free user
        try:
            missed = get_missed_paid_alerts(days=7)
            pro_alerts = missed["pro_only_alerts"]
            conv_missed = missed["pro_only_convergences"]
        except Exception:
            pro_alerts, conv_missed = 0, 0

        msg = (
            "📊 <b>Your Free Account</b>\n\n"
            "<b>Last 7 days — what you missed:</b>\n"
            f"  • {pro_alerts} Pro-only trade alerts\n"
            f"  • {conv_missed} convergence signals\n\n"
            f"Upgrade to see them in real-time.\n\n"
            f"→ <a href='{SUBSCRIBE_LINK}'>Subscribe ($29/mo)</a>\n"
            f"→ /trial (3 days free)")
        await update.message.reply_html(msg)
        return

    status_label = {
        "active": "✅ Active", "trial": "🎁 Trial", "past_due": "⚠️ Payment overdue",
        "cancelled": "❌ Cancelled", "expired": "❌ Expired", "inactive": "Inactive",
    }.get(sub["status"], sub["status"])

    msg = (
        f"<b>WhalePulse Account</b>\n\n"
        f"Plan: {sub['plan'].title()}\n"
        f"Status: {status_label}\n"
        f"Total paid: ${sub['total_paid']:.2f}\n")

    if sub.get("expires_at") and sub["expires_at"] > 0:
        from datetime import datetime, timezone
        exp = datetime.fromtimestamp(sub["expires_at"], tz=timezone.utc).strftime("%Y-%m-%d")
        label = "Trial ends" if sub["status"] == "trial" else "Renews"
        msg += f"{label}: {exp}\n"

    # Referral stats
    try:
        ref_count = get_referral_count(user.id)
        rewarded = get_completed_referral_count(user.id)
        if ref_count > 0:
            msg += f"\nReferrals: {ref_count} invited, {rewarded} subscribed\n"
    except Exception:
        pass

    if sub["status"] == "active" and sub.get("stripe_customer_id"):
        portal_url = create_billing_portal(sub["stripe_customer_id"])
        if portal_url:
            keyboard = [[InlineKeyboardButton("Manage Subscription", url=portal_url)]]
            await update.message.reply_html(msg, reply_markup=InlineKeyboardMarkup(keyboard))
            return

    await update.message.reply_html(msg)


async def cmd_trial(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    sub = get_subscriber(user.id)

    if sub and sub["status"] == "active":
        await update.message.reply_text("You're already a Pro member! Send /account to manage.")
        return
    if sub and sub["status"] == "trial":
        await update.message.reply_text("Your trial is already active! Check /account for details.")
        return

    # Atomically claim the trial slot — prevents double-grant from concurrent requests
    if not claim_trial(user.id, user.username or ""):
        await update.message.reply_html(
            f"You've already used your free trial.\n\n"
            f"Ready to subscribe? <a href='{SUBSCRIBE_LINK}'>Subscribe →</a>")
        return

    invite = create_pro_invite(user.id, expire_days=1)
    if not invite:
        # Roll back the claim so user can retry
        upsert_subscriber(user.id, used_trial=0)
        await update.message.reply_text(
            "Couldn't create trial access right now. Please try again or contact support.")
        return

    expires_at = int(time.time()) + (TRIAL_DAYS * 86400)
    upsert_subscriber(user.id,
                      telegram_username=user.username or "",
                      plan="pro",
                      status="trial",
                      started_at=int(time.time()),
                      expires_at=expires_at,
                      in_channel=1)

    from datetime import datetime, timezone
    exp_str = datetime.fromtimestamp(expires_at, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    await update.message.reply_html(
        f"🎁 <b>Your 3-day Pro trial is active!</b>\n\n"
        f"Join the Pro channel (link expires in 24h, single-use):\n{invite}\n\n"
        f"Trial ends: {exp_str}\n\n"
        f"You'll get real-time whale alerts, convergence signals, and AI analysis.\n"
        f"To keep access after your trial: <a href='{SUBSCRIBE_LINK}'>Subscribe →</a>")


async def cmd_refer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    code = f"wp{user.id}"
    bot_username = (await context.bot.get_me()).username
    link = f"https://t.me/{bot_username}?start=ref_{code}"

    try:
        referred = get_referral_count(user.id)
        completed = get_completed_referral_count(user.id)
    except Exception:
        referred, completed = 0, 0

    await update.message.reply_html(
        f"🔗 <b>Your Referral Link</b>\n\n"
        f"<code>{link}</code>\n\n"
        f"Share this link. When a friend subscribes using it:\n"
        f"  • They get 20% off their first month\n"
        f"  • You get 1 month free\n\n"
        f"Your stats: {referred} referred, {completed} subscribed")


async def cmd_watch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not context.args:
        # Show current watchlist
        keywords = get_user_watchlist(user.id)
        if keywords:
            kw_list = "\n".join(f"  • {k}" for k in keywords)
            await update.message.reply_text(
                f"Your watchlist:\n{kw_list}\n\n"
                "Use /watch [keyword] to add, /unwatch [keyword] to remove.")
        else:
            await update.message.reply_text(
                "Your watchlist is empty.\n\n"
                "Usage: /watch bitcoin\n"
                "You'll get a DM whenever a whale trades in a matching market.")
        return

    keyword = " ".join(context.args).lower().strip()
    keywords = get_user_watchlist(user.id)
    if len(keywords) >= 10:
        await update.message.reply_text("Watchlist limit is 10 keywords. Use /unwatch to remove some.")
        return

    added = add_watchlist(user.id, keyword)
    if added:
        await update.message.reply_text(
            f"✅ Added '{keyword}' to your watchlist.\n"
            "You'll get a DM when whales trade in matching markets.")
    else:
        await update.message.reply_text(f"'{keyword}' is already in your watchlist.")


async def cmd_unwatch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not context.args:
        await update.message.reply_text("Usage: /unwatch [keyword]")
        return
    keyword = " ".join(context.args).lower().strip()
    remove_watchlist(user.id, keyword)
    await update.message.reply_text(f"Removed '{keyword}' from your watchlist.")


async def cmd_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not context.args:
        await update.message.reply_text(
            "Send your feedback:\n/feedback [your message]\n\nExample: /feedback I'd love to see ETH markets tracked")
        return
    feedback_text = " ".join(context.args)
    username = f"@{user.username}" if user.username else str(user.id)
    try:
        from shared.notifier import send_admin_alert
        send_admin_alert(f"💬 Feedback from {username}:\n\n{feedback_text}", silent=False)
        await update.message.reply_text("Thanks! Your feedback has been sent to the team. 🙏")
    except Exception:
        await update.message.reply_text("Couldn't send feedback right now. Please try again.")


async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    sub = get_subscriber(user.id)

    if not sub or sub["status"] not in ("active", "trial"):
        await update.message.reply_html(
            f"📈 <b>Signal History</b> — Pro only\n\n"
            f"<a href='{SUBSCRIBE_LINK}'>Subscribe to access →</a>")
        return

    days = 7
    if context.args:
        try:
            d = int(context.args[0])
            if d in (7, 30, 90):
                days = d
        except ValueError:
            pass

    history = get_alert_history(days)

    msg = f"📈 <b>Last {days} Days</b>\n\n"
    msg += f"Pro alerts sent: {history['alerts']}\n"
    if history["resolved"] > 0:
        wr = history["wins"] / history["resolved"] * 100
        msg += f"Markets resolved: {history['resolved']} ({wr:.0f}% correct)\n"
    else:
        msg += "Markets resolved: 0 (still accumulating data)\n"

    if history["top_signals"]:
        msg += "\n<b>Top calls:</b>\n"
        for t in history["top_signals"]:
            icon = "✅" if t["won"] else "❌"
            msg += f"  {icon} <i>{t['title'][:55]}</i> (score {t['signal_score']})\n"

    msg += "\nUsage: /history 7 · /history 30 · /history 90"
    await update.message.reply_html(msg)


async def cmd_subs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command — subscriber stats."""
    user = update.effective_user
    if str(user.id) != ADMIN_CHAT_ID:
        return
    stats = get_subscriber_stats()
    active = get_active_subscribers()
    msg = (f"SUBSCRIBER STATS\n\n"
           f"Active: {stats['active']}\n"
           f"Trials: {stats.get('trials', 0)}\n"
           f"Total signups: {stats['total']}\n"
           f"Revenue: ${stats['total_revenue']:.2f}\n\n")
    if active:
        msg += "Active subscribers:\n"
        for s in active[:20]:
            status_tag = " [trial]" if s["status"] == "trial" else ""
            msg += f"  @{s['telegram_username'] or s['telegram_id']}{status_tag}\n"
    await update.message.reply_text(msg)


async def cmd_admin_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: system status."""
    if str(update.effective_user.id) != ADMIN_CHAT_ID:
        return
    import psutil, subprocess
    cpu = psutil.cpu_percent(interval=1)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    stats = get_stats()
    costs = get_cost_summary()
    services = ["whalepulse-tracker", "whalepulse-scanner", "whalepulse-payments",
                "whalepulse-subscribe", "whalepulse-subcheck"]
    svc_status = {}
    for svc in services:
        try:
            r = subprocess.run(["systemctl", "is-active", svc], capture_output=True, text=True, timeout=5)
            svc_status[svc] = "OK" if r.stdout.strip() == "active" else "DOWN"
        except Exception:
            svc_status[svc] = "?"
    msg = (f"WHALEPULSE STATUS\n\nServer: CPU {cpu}% | RAM {mem.percent}% | Disk {disk.percent}%\n\nServices:\n")
    for s, st in svc_status.items():
        msg += f"  {st} {s}\n"
    msg += (f"\nData: {stats['active_wallets']} wallets | {stats['total_trades']} trades | "
            f"{stats['trades_24h']} today\nAPI: ${costs['today_cost']:.4f} today ({costs['today_calls']} calls)")
    await update.message.reply_text(msg)


async def cmd_admin_whales(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: top whale wallets."""
    if str(update.effective_user.id) != ADMIN_CHAT_ID:
        return
    wallets = get_top_wallets(15)
    if not wallets:
        await update.message.reply_text("No wallets yet.")
        return
    msg = "TOP WHALES\n\n"
    for i, w in enumerate(wallets, 1):
        wt = w["total_trades"] or 0
        wr = f"{(w['wins']/wt*100):.0f}%" if wt > 5 else "NEW"
        msg += f"{i}. {w['name'] or w['address'][:10]} - {w['score']:.0f}pts | WR: {wr} | PnL: ${w['total_pnl']:,.0f}\n"
    await update.message.reply_text(msg)


async def cmd_admin_trades(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: recent trades."""
    if str(update.effective_user.id) != ADMIN_CHAT_ID:
        return
    from datetime import datetime, timezone
    with get_db() as db:
        trades = db.execute(
            "SELECT t.*, w.name, w.score FROM trades t "
            "JOIN wallets w ON t.wallet_address = w.address "
            "ORDER BY t.timestamp DESC LIMIT 10").fetchall()
    if not trades:
        await update.message.reply_text("No trades yet.")
        return
    msg = "RECENT TRADES\n\n"
    for t in trades:
        name = t["name"] or t["wallet_address"][:8]
        ts = datetime.fromtimestamp(t["timestamp"], tz=timezone.utc).strftime("%H:%M")
        msg += f"{t['side']} {name} @ {t['price']*100:.0f}% | ${t['usdc_value']:,.0f} | Sig: {t['signal_score']}\n"
        msg += f"  {t['title'][:60]}\n  {ts} UTC\n\n"
    await update.message.reply_text(msg)


async def cmd_admin_costs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: API costs."""
    if str(update.effective_user.id) != ADMIN_CHAT_ID:
        return
    costs = get_cost_summary()
    msg = (f"API COSTS\nToday: ${costs['today_cost']:.4f} ({costs['today_calls']} calls)\n"
           f"Total: ${costs['total_cost']:.4f} ({costs['total_calls']} calls)\n"
           f"Est monthly: ${costs['today_cost']*30:.2f}")
    await update.message.reply_text(msg)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html(
        "WhalePulse Commands\n\n"
        f"<a href='{SUBSCRIBE_LINK}'>Get Pro</a> — $29/mo or $249/yr\n"
        "/trial — 3-day free Pro trial\n"
        "/account — Your subscription status\n"
        "/history [7|30|90] — Signal accuracy history (Pro)\n"
        "/watch [keyword] — Get DMs for matching markets\n"
        "/unwatch [keyword] — Remove from watchlist\n"
        "/refer — Get your referral link\n"
        "/feedback [message] — Send feedback\n"
        "/help — This message")


def main():
    print("=" * 50)
    print("  WhalePulse Subscription Bot")
    print("=" * 50)

    init_payments_db()
    init_db()
    token = TELEGRAM_TOKEN
    if not token:
        print("ERROR: No TELEGRAM_BOT_TOKEN")
        sys.exit(1)

    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("subscribe", cmd_subscribe))
    app.add_handler(CommandHandler("account", cmd_account))
    app.add_handler(CommandHandler("trial", cmd_trial))
    app.add_handler(CommandHandler("refer", cmd_refer))
    app.add_handler(CommandHandler("watch", cmd_watch))
    app.add_handler(CommandHandler("unwatch", cmd_unwatch))
    app.add_handler(CommandHandler("feedback", cmd_feedback))
    app.add_handler(CommandHandler("history", cmd_history))
    app.add_handler(CommandHandler("subs", cmd_subs))
    app.add_handler(CommandHandler("status", cmd_admin_status))
    app.add_handler(CommandHandler("whales", cmd_admin_whales))
    app.add_handler(CommandHandler("trades", cmd_admin_trades))
    app.add_handler(CommandHandler("costs", cmd_admin_costs))
    app.add_handler(CommandHandler("help", cmd_help))

    print("Subscription bot running.")
    app.run_polling()


if __name__ == "__main__":
    main()
