"""
WhalePulse Notifier — Two-tier Telegram alerts
"""
import os
import time
import httpx
from pathlib import Path
from dotenv import load_dotenv
from shared.database import log_alert

load_dotenv(Path(__file__).parent.parent / "config" / ".env")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
PAID_CHAT_ID = os.getenv("TELEGRAM_PRO_CHANNEL_ID") or os.getenv("TELEGRAM_CHAT_ID")
FREE_CHAT_ID = os.getenv("TELEGRAM_FREE_CHANNEL_ID")
ADMIN_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
BOT_USERNAME = os.getenv("BOT_USERNAME", "PulseWhale_bot")
SUBSCRIBE_LINK = f"https://t.me/{BOT_USERNAME}?start=subscribe"
MAX_MSG_LEN = 4000

CATEGORY_EMOJI = {
    "politics": "🇺🇸", "crypto": "₿", "sports": "🏀",
    "economy": "📈", "tech": "💻", "world": "🌍",
    "culture": "🎬", "other": "📊",
}

def _send_telegram(chat_id, text, silent=False, parse_mode="HTML"):
    if not TELEGRAM_TOKEN or not chat_id:
        print(f"  [NO TELEGRAM] {text[:100]}")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    if len(text) > MAX_MSG_LEN:
        text = text[:MAX_MSG_LEN - 20] + "\n\n[truncated]"
    try:
        resp = httpx.post(url, json={
            "chat_id": chat_id, "text": text, "parse_mode": parse_mode,
            "disable_notification": silent, "disable_web_page_preview": True}, timeout=15)
        if resp.status_code == 200:
            return True
        # Fallback: strip HTML tags
        plain = (text.replace("<b>","").replace("</b>","").replace("<i>","")
                 .replace("</i>","").replace("<code>","").replace("</code>",""))
        resp2 = httpx.post(url, json={
            "chat_id": chat_id, "text": plain,
            "disable_notification": silent, "disable_web_page_preview": True}, timeout=15)
        return resp2.status_code == 200
    except Exception as e:
        print(f"  [TELEGRAM ERROR] {e}")
        return False

def send_paid_alert(text, alert_type="trade", silent=False):
    success = _send_telegram(PAID_CHAT_ID, text, silent=silent)
    if success:
        log_alert(alert_type, "paid", text[:500])
        print(f"  [PAID ALERT] {text[:80]}")
    return success

def send_free_alert(text, alert_type="trade", silent=False):
    if not FREE_CHAT_ID: return False
    success = _send_telegram(FREE_CHAT_ID, text, silent=silent)
    if success:
        log_alert(alert_type, "free", text[:500])
        print(f"  [FREE ALERT] {text[:80]}")
    return success

def send_admin_alert(text, silent=True):
    return _send_telegram(ADMIN_CHAT_ID, text, silent=silent)

def send_dm(telegram_id, text):
    """Send a direct message to a specific user."""
    return _send_telegram(str(telegram_id), text)

def _signal_bar(score):
    filled = min(score // 10, 10)
    return "█" * filled + "░" * (10 - filled)

# ── Trade alerts ─────────────────────────────────────────────────────────────

def format_whale_trade(trade, wallet, signal_score=0, free=False):
    side = trade["side"]                        # BUY or SELL
    outcome = trade.get("outcome", "Yes")       # Yes or No
    usdc_val = trade.get("usdc_value", 0) or (trade["size"] * trade["price"])
    avg_size = wallet.get("avg_trade_size", 0) or 1
    ratio = usdc_val / avg_size if avg_size > 0 else 1
    name = wallet.get("name") or wallet.get("pseudonym") or trade["wallet_address"][:10]
    price_pct = f"{trade['price'] * 100:.0f}%"
    title = trade.get("title", "Unknown")[:120]
    cat = trade.get("category", "other")
    cat_emoji = CATEGORY_EMOJI.get(cat, "📊")
    score_bar = _signal_bar(signal_score)
    w_total = wallet.get("total_trades", 0) or wallet.get("w_total", 0)
    wins = wallet.get("wins", 0)
    winrate = f"{(wins / w_total * 100):.0f}%" if w_total > 5 else "NEW"

    if ratio >= 3: conviction = f"🔥🔥🔥 {ratio:.1f}x their normal bet"
    elif ratio >= 2: conviction = f"🔥🔥 {ratio:.1f}x their normal bet"
    elif ratio >= 1.2: conviction = f"🔥 {ratio:.1f}x their normal bet"
    else: conviction = "Normal size"

    if side == "BUY":
        if outcome.lower() == "yes":
            header = f"🟢 <b>WHALE BET — YES</b> {cat_emoji}"
            action = "betting <b>YES</b> — thinks this <b>will happen</b>"
        else:
            header = f"🔴 <b>WHALE BET — NO</b> {cat_emoji}"
            action = "betting <b>NO</b> — thinks this <b>won't happen</b>"
    else:
        header = f"🔄 <b>WHALE EXIT</b> {cat_emoji}"
        action = f"exiting their {outcome} position — taking profits or changing view"

    if free:
        # Free version: show the market so they feel FOMO, hide everything actionable
        return (
            f"{header}\n\n"
            f"<i>{title}</i>\n\n"
            f"A tracked whale just entered this market.\n"
            f"Signal: {score_bar} {signal_score}/100\n\n"
            f"<i>⚡ Pro members got this 30 min ago with: whale identity, "
            f"position size, entry odds, and direct link.\n"
            f"<a href='{SUBSCRIBE_LINK}'>Subscribe ($29/mo) →</a></i>")

    msg = (
        f"{header}\n\n"
        f"<i>{title}</i>\n\n"
        f"<b>{name}</b> is {action}\n"
        f"Amount: <b>${usdc_val:,.0f}</b> · Market odds: {price_pct}\n"
        f"Conviction: {conviction}\n\n"
        f"Trader: {w_total} trades · Win rate: {winrate} · Score: {wallet.get('score', 50):.0f}/100\n"
        f"Signal: {score_bar} {signal_score}/100\n\n"
        f"👉 https://polymarket.com/event/{trade.get('event_slug', trade.get('slug', ''))}")
    return msg

def format_score_breakdown(trade, wallet, signal_score):
    """One-line score breakdown appended to high-signal paid alerts (score ≥ 70)."""
    wallet_score = wallet.get("score", 50)
    rep = min(35, int(wallet_score * 0.35))
    usdc_val = trade.get("usdc_value", 0) or (trade["size"] * trade["price"])
    avg_size = wallet.get("avg_trade_size", 0) or 1
    r = usdc_val / avg_size if avg_size > 0 else 0
    if r >= 5: size_pts = 25
    elif r >= 3: size_pts = 20
    elif r >= 2: size_pts = 15
    elif usdc_val >= 10000: size_pts = 20
    elif usdc_val >= 5000: size_pts = 15
    elif r >= 1: size_pts = 10
    else: size_pts = 5 if usdc_val >= 1000 else 0
    w_total = wallet.get("total_trades", 0) or wallet.get("w_total", 0)
    wins = wallet.get("wins", 0)
    if w_total > 10: wr_pts = min(20, int(wins / w_total * 25))
    elif w_total > 5: wr_pts = min(15, int(wins / w_total * 20))
    else: wr_pts = 0
    tier = wallet.get("tier", 2)
    tier_pts = 10 if tier == 1 else 5
    price = float(trade.get("price", 0.5))
    side = trade.get("side", "BUY")
    if side == "BUY" and price < 0.15: conv_pts = 10
    elif (side == "BUY" and price > 0.85) or (side == "SELL" and price > 0.85): conv_pts = 8
    elif side == "BUY" and price < 0.30: conv_pts = 7
    else: conv_pts = 0
    parts = [
        f"Rep {rep}/35",
        f"Size {size_pts}/25",
    ]
    if wr_pts > 0:
        parts.append(f"WR {wr_pts}/20")
    parts.append(f"Tier {tier_pts}/10")
    if conv_pts > 0:
        parts.append(f"Price {conv_pts}/10")
    return f"<i>Score breakdown: {' · '.join(parts)}</i>"


# ── Convergence alerts ────────────────────────────────────────────────────────

def format_convergence_alert(event, wallet_details=None):
    """Full convergence alert with optional per-wallet score breakdown."""
    score_bar = _signal_bar(event["signal_score"])
    wallet_names = event.get("wallet_names", [])
    side = event["dominant_side"]
    side_plain = "YES — they think this <b>will happen</b>" if side.lower() == "yes" else "NO — they think this <b>won't happen</b>"

    msg = (
        f"🐋🐋🐋 <b>{event['wallet_count']} WHALES AGREE</b>\n\n"
        f"<i>{event['title'][:120]}</i>\n\n"
        f"All betting {side_plain}\n"
        f"Combined: <b>${event['total_size']:,.0f}</b>\n\n")

    if wallet_details:
        for wd in wallet_details[:5]:
            msg += f"  · {wd['name']} (score {wd['score']:.0f}) — ${wd['size']:,.0f}\n"
    elif wallet_names:
        names_str = ", ".join(wallet_names[:5])
        if len(wallet_names) > 5:
            names_str += f" +{len(wallet_names) - 5} more"
        msg += f"Traders: {names_str}\n"

    msg += (f"\nSignal: {score_bar} {event['signal_score']}/100\n\n"
            f"👉 https://polymarket.com/event/{event.get('slug', '')}")
    return msg

def format_scanner_teaser(pct, category):
    """Free channel teaser for a price move alert — no market title, no link."""
    direction = "surged" if pct > 0 else "dropped"
    cat_emoji = CATEGORY_EMOJI.get(category, "📊")
    icon = "📈" if pct > 0 else "📉"
    return (
        f"{icon} <b>MARKET MOVE</b> {cat_emoji}\n\n"
        f"A {category} market just {direction} {abs(pct):.0f}%.\n"
        f"Pro members are seeing the full details right now.\n\n"
        f"<i><a href='{SUBSCRIBE_LINK}'>Get whale alerts + price moves →</a></i>")


def format_convergence_teaser(event):
    """Teaser for free channel — convergence signals with score 40–69."""
    return (
        f"🐋 <b>{event['wallet_count']} WHALES JUST AGREED</b>\n\n"
        f"Multiple top traders just bet the same direction on a market.\n"
        f"Signal score: {event['signal_score']}/100 — Pro members can see which market.\n\n"
        f"<i><a href='{SUBSCRIBE_LINK}'>Unlock this signal →</a></i>")

# ── Digests ───────────────────────────────────────────────────────────────────

def format_daily_digest(wallets, trades_24h, convergences, ai_summary="", sub_count=0):
    msg = f"📊 <b>DAILY DIGEST</b>\n\n"
    msg += "<b>🏆 Top Tracked Traders</b>\n"
    for i, w in enumerate(wallets[:5], 1):
        wt = w["total_trades"] or 0
        wr = f"{(w['wins'] / wt * 100):.0f}% win rate" if wt > 5 else "new"
        msg += f"  {i}. <b>{w['name'] or w['address'][:8]}</b> — score {w['score']:.0f}/100 · {wr}\n"
    msg += f"\n<b>📈 Last 24h</b>\n  Whale trades detected: {trades_24h}\n  Multi-whale agreement signals: {convergences}\n"
    if ai_summary:
        msg += f"\n<b>🤖 AI Summary</b>\n{ai_summary[:800]}\n"
    if sub_count > 0:
        msg += f"\n<i>Join {sub_count} Pro members tracking the whales — <a href='{SUBSCRIBE_LINK}'>Subscribe →</a></i>"
    else:
        msg += f"\n<i>WhalePulse — track the biggest players on Polymarket</i>"
    return msg

def format_weekly_digest(wallets, weekly_stats, top_trades, ai_summary="", sub_count=0):
    msg = f"📊 <b>WEEKLY DIGEST</b>\n\n"
    msg += "<b>🏆 Top Tracked Traders This Week</b>\n"
    for i, w in enumerate(wallets[:5], 1):
        wt = w["total_trades"] or 0
        wr = f"{(w['wins'] / wt * 100):.0f}% win rate" if wt > 5 else "new"
        msg += f"  {i}. <b>{w['name'] or w['address'][:8]}</b> — score {w['score']:.0f}/100 · {wr}\n"
    msg += f"\n<b>📈 This Week</b>\n"
    msg += f"  Whale trades detected: {weekly_stats['trades_7d']}\n"
    msg += f"  Multi-whale agreement signals: {weekly_stats['convergences_7d']}\n"
    if weekly_stats["resolved_7d"] > 0:
        wr = weekly_stats["wins_7d"] / weekly_stats["resolved_7d"] * 100
        msg += f"  Signal accuracy: {wr:.0f}% ({weekly_stats['wins_7d']} of {weekly_stats['resolved_7d']} calls correct)\n"
    if top_trades:
        msg += f"\n<b>🎯 Best Calls This Week</b>\n"
        for t in top_trades[:3]:
            name = t.get("name") or t["wallet_address"][:8]
            outcome_icon = "✅" if t["won"] else "❌"
            direction = "YES" if t.get("outcome", "").lower() == "yes" else "NO"
            msg += f"  {outcome_icon} {name} called {direction} — <i>{t['title'][:55]}</i>\n"
    if ai_summary:
        msg += f"\n<b>🤖 AI Summary</b>\n{ai_summary[:600]}\n"
    if sub_count > 0:
        msg += f"\n<i>Join {sub_count} Pro members tracking the whales — <a href='{SUBSCRIBE_LINK}'>Subscribe →</a></i>"
    return msg

def format_whale_of_week(wallet, top_trades):
    name = wallet.get("name") or wallet.get("wallet_address", "")[:10]
    wt = wallet.get("trade_count", 0)
    wins = wallet.get("wins", 0)
    wr = f"{(wins / wt * 100):.0f}%" if wt > 0 else "—"
    week_pnl = wallet.get("week_pnl", 0) or 0
    msg = (
        f"🐋 <b>WHALE OF THE WEEK</b>\n\n"
        f"<b>{name}</b>\n"
        f"Score: {wallet.get('score', 0):.0f}/100 | Win rate: {wr}\n"
        f"Week PnL: ${week_pnl:+,.0f}\n\n"
        f"<b>Top calls:</b>\n")
    for t in top_trades[:5]:
        icon = "✅" if t["won"] else "❌"
        msg += f"  {icon} {t['side']} {t.get('outcome', '')} @ {t['price']*100:.0f}% — <i>{t['title'][:50]}</i>\n"
    return msg

def format_category_leaderboard(category_stats, days=7):
    msg = f"📊 <b>Category Performance — Last {days} Days</b>\n\n"
    medals = ["🏆", "🥈", "🥉"]
    for i, cat in enumerate(category_stats[:6]):
        total = cat["total"]
        wins = cat["wins"]
        wr = wins / total * 100 if total > 0 else 0
        cat_emoji = CATEGORY_EMOJI.get(cat["category"], "📊")
        medal = medals[i] if i < 3 else "  "
        warn = " ⚠️ caution" if wr < 45 and total >= 3 else ""
        msg += f"{medal} {cat_emoji} <b>{cat['category'].title()}</b>: {wins}/{total} ({wr:.0f}%){warn}\n"
    if not category_stats:
        msg += "<i>Not enough resolved trades yet.</i>"
    return msg

# ── Resolution posts ─────────────────────────────────────────────────────────

def format_resolution_post(trade, wallet_name, won):
    icon = "✅" if won else "❌"
    result_label = "RIGHT" if won else "WRONG"
    side = trade.get("side", "BUY")
    outcome = trade.get("outcome", "YES")
    price_pct = f"{trade['price'] * 100:.0f}%"
    title = trade.get("title", "Unknown")[:120]
    score = trade.get("signal_score", 0)
    slug = trade.get("event_slug") or trade.get("slug", "")
    from datetime import datetime, timezone
    ts = datetime.fromtimestamp(trade.get("timestamp", 0), tz=timezone.utc).strftime("%b %d")
    cat = trade.get("category", "other")
    cat_emoji = CATEGORY_EMOJI.get(cat, "📊")
    msg = (
        f"{icon} <b>WHALE WAS {result_label}</b> {cat_emoji}\n\n"
        f"<i>{title}</i>\n\n"
        f"{wallet_name} {side} {outcome} @ {price_pct} on {ts}\n"
        f"Signal score: {score}/100\n\n"
        f"https://polymarket.com/event/{slug}")
    return msg


# ── Wallet report ─────────────────────────────────────────────────────────────

def format_wallet_report(wallet, recent_trades, positions):
    w = wallet
    name = w["name"] or w["address"][:12]
    wt = w["total_trades"] or 0
    wr = f"{(w['wins'] / wt * 100):.0f}%" if wt > 5 else "NEW"
    msg = (f"👤 <b>WHALE REPORT: {name}</b>\n{'-' * 28}\n"
           f"Score: <b>{w['score']:.0f}/100</b> | Win Rate: {wr}\n"
           f"Trades: {wt} | PnL: ${w['total_pnl']:,.0f}\nAvg Trade: ${w['avg_trade_size']:,.0f}\n")
    if w.get("best_category"):
        msg += f"Best at: {w['best_category']} ({w['best_category_winrate']:.0f}%)\n"
    if positions:
        msg += f"\n<b>Open Positions ({len(positions)})</b>\n"
        for p in positions[:5]:
            emoji = "📈" if (p.get("cash_pnl") or 0) > 0 else "📉"
            msg += f"  {emoji} {p['title'][:40]} — ${p.get('current_value', 0):,.0f} ({p.get('percent_pnl', 0):+.0f}%)\n"
    if recent_trades:
        msg += f"\n<b>Recent Trades</b>\n"
        for t in recent_trades[:5]:
            se = "🟢" if t["side"] == "BUY" else "🔴"
            msg += f"  {se} {t['side']} {t.get('outcome', '')} @ {t['price']*100:.0f}% — ${t['size']*t['price']:,.0f}\n"
            msg += f"     <i>{t['title'][:50]}</i>\n"
    return msg
