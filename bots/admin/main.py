#!/usr/bin/env python3
"""
WhalePulse Admin Bot — Telegram commands
"""
import sys, os, subprocess, psutil
from datetime import datetime, timezone
from pathlib import Path
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / "config" / ".env")

from shared.database import init_db, get_top_wallets, get_wallet, get_active_wallets, get_wallet_trades, get_stats, get_db
from shared.ai_client import get_cost_summary
from shared.notifier import format_wallet_report
from shared.polymarket_api import get_wallet_positions

ADMIN_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


def _is_admin(update: Update) -> bool:
    return str(update.effective_user.id) == ADMIN_CHAT_ID


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    cpu = psutil.cpu_percent(interval=1)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    stats = get_stats()
    costs = get_cost_summary()
    services = {}
    for svc in ["whalepulse-tracker", "whalepulse-scanner", "whalepulse-admin"]:
        try:
            r = subprocess.run(["systemctl", "is-active", svc], capture_output=True, text=True, timeout=5)
            services[svc] = "OK" if r.stdout.strip() == "active" else "DOWN"
        except: services[svc] = "?"
    msg = (f"WHALEPULSE STATUS\n\nServer: CPU {cpu}% | RAM {mem.percent}% | Disk {disk.percent}%\n\n"
           f"Bots:\n")
    for s, st in services.items(): msg += f"  {st} {s}\n"
    msg += (f"\nData: {stats['active_wallets']} wallets | {stats['total_trades']} trades | "
            f"{stats['trades_24h']} today\n\nAPI: ${costs['today_cost']:.4f} today ({costs['today_calls']} calls)")
    await update.message.reply_text(msg)

async def cmd_whales(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
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

async def cmd_trades(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
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

async def cmd_costs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    costs = get_cost_summary()
    msg = (f"API COSTS\nToday: ${costs['today_cost']:.4f} ({costs['today_calls']} calls)\n"
           f"Total: ${costs['total_cost']:.4f} ({costs['total_calls']} calls)\n"
           f"Est monthly: ${costs['today_cost']*30:.2f}")
    await update.message.reply_text(msg)

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = ("WhalePulse Admin\n\n/status - System health\n/whales - Top wallets\n"
           "/trades - Recent trades\n/costs - API spending\n/help - This message")
    await update.message.reply_text(msg)

def main():
    print("=" * 50)
    print("  WhalePulse Admin Bot")
    print("=" * 50)
    init_db()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("ERROR: No TELEGRAM_BOT_TOKEN")
        sys.exit(1)
    app = ApplicationBuilder().token(token).build()
    for cmd, fn in [("status", cmd_status), ("whales", cmd_whales), ("trades", cmd_trades),
                    ("costs", cmd_costs), ("help", cmd_help), ("start", cmd_help)]:
        app.add_handler(CommandHandler(cmd, fn))
    print("Admin bot running. Send /help in Telegram.")
    app.run_polling()

if __name__ == "__main__":
    main()
