#!/usr/bin/env python3
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
    init_payments_db, get_expired_subscribers, get_expired_trials, upsert_subscriber
)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
PRO_CHANNEL_ID = os.getenv("TELEGRAM_PRO_CHANNEL_ID", "")
ADMIN_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "PulseWhale_bot")
SUBSCRIBE_LINK = f"https://t.me/{BOT_USERNAME}?start=subscribe"

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
    removed = 0

    # Expired paid subscribers
    expired = get_expired_subscribers()
    for sub in expired:
        print(f"  Expired paid: @{sub['telegram_username']} ({sub['telegram_id']})")
        kicked = kick_user(sub["telegram_id"])
        upsert_subscriber(sub["telegram_id"], status="expired", in_channel=0)
        send_telegram(sub["telegram_id"],
            f"Your WhalePulse Pro access has expired.\n\n"
            f"Reactivate here: {SUBSCRIBE_LINK}")
        if kicked: removed += 1

    # Expired trial subscribers
    expired_trials = get_expired_trials()
    for sub in expired_trials:
        print(f"  Expired trial: @{sub['telegram_username']} ({sub['telegram_id']})")
        kicked = kick_user(sub["telegram_id"])
        upsert_subscriber(sub["telegram_id"], status="expired", in_channel=0)
        send_telegram(sub["telegram_id"],
            f"Your WhalePulse Pro trial has ended.\n\n"
            f"To keep getting real-time whale alerts:\n"
            f"{SUBSCRIBE_LINK} ($29/mo)")
        if kicked: removed += 1

    total_checked = len(expired) + len(expired_trials)
    if removed:
        send_telegram(ADMIN_CHAT_ID, f"Removed {removed} expired subscribers/trials")
    print(f"  Checked {total_checked} expired ({len(expired)} paid, {len(expired_trials)} trials), removed {removed}")

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
