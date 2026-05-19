#!/usr/bin/env python3
"""
WhalePulse Market Scanner
"""
import sys, time, json, schedule
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from shared.polymarket_api import get_gamma_markets, classify_market_category
from shared.notifier import send_paid_alert, send_free_alert, send_admin_alert, format_scanner_teaser
from shared.database import init_db, kv_set, kv_get

_previous_prices = {}
_alerted_markets = set()


def _load_state():
    global _previous_prices, _alerted_markets
    saved_prices = kv_get("scanner_previous_prices", {})
    saved_alerted = kv_get("scanner_alerted_markets", [])
    _previous_prices.update(saved_prices)
    _alerted_markets.update(saved_alerted)


def _save_state():
    kv_set("scanner_previous_prices", _previous_prices)
    kv_set("scanner_alerted_markets", list(_alerted_markets))
PRICE_MOVE_PCT = 10
MIN_VOLUME_24H = 50000
SCAN_INTERVAL = 15

def log(msg):
    print(f"  {msg}")

def scan_markets():
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n[{ts}] Scanning markets...")
    try:
        markets = get_gamma_markets(limit=50, order="volume24hr")
    except Exception as e:
        log(f"Failed: {e}")
        return
    if not markets:
        log("No markets")
        return
    alerts = []
    tracked = 0
    for m in markets:
        slug = m.get("slug", "")
        if not slug: continue
        try:
            outcomes = m.get("outcomePrices", "")
            if isinstance(outcomes, str):
                prices = json.loads(outcomes) if outcomes else []
            elif isinstance(outcomes, list):
                prices = outcomes
            else: continue
            if len(prices) < 2: continue
            yes_price = float(prices[0])
            no_price = float(prices[1])
        except (json.JSONDecodeError, ValueError, IndexError): continue
        try:
            volume_24h = float(m.get("volume24hr", 0) or 0)
        except (ValueError, TypeError):
            volume_24h = 0
        if volume_24h < MIN_VOLUME_24H: continue
        tracked += 1
        title = m.get("question", m.get("title", "Unknown"))
        category = classify_market_category(title)
        current = {"yes_price": yes_price, "no_price": no_price, "volume": volume_24h,
                   "timestamp": time.time(), "title": title, "category": category,
                   "event_slug": m.get("eventSlug", slug)}
        if slug in _previous_prices:
            prev = _previous_prices[slug]
            if prev["yes_price"] > 0.01:
                pct = ((yes_price - prev["yes_price"]) / prev["yes_price"]) * 100
                if abs(pct) >= PRICE_MOVE_PCT and slug not in _alerted_markets:
                    d = "\U0001f4c8" if pct > 0 else "\U0001f4c9"
                    alerts.append({"type": "price", "title": title, "slug": slug,
                                   "event_slug": current["event_slug"], "direction": d,
                                   "pct": pct, "yes": yes_price, "prev": prev["yes_price"],
                                   "vol": volume_24h, "cat": category})
                    _alerted_markets.add(slug)
        _previous_prices[slug] = current
    _save_state()  # Save before any clearing
    if len(_alerted_markets) > 200:
        _alerted_markets.clear()  # In-memory only; saved state preserved for restart
    log(f"Tracked {tracked} markets | Alerts: {len(alerts)}")
    for a in alerts:
        direction_plain = "surged" if a["pct"] > 0 else "crashed"
        direction_meaning = "more likely to happen" if a["pct"] > 0 else "less likely to happen"
        cat_emojis = {"politics": "🇺🇸", "crypto": "₿", "sports": "🏀", "economy": "📈", "tech": "💻", "world": "🌍", "culture": "🎬"}
        cat_emoji = cat_emojis.get(a["cat"], "📊")
        msg = (f"{'📈' if a['pct'] > 0 else '📉'} <b>MARKET MOVE</b> {cat_emoji}\n\n"
               f"<i>{a['title'][:120]}</i>\n\n"
               f"The market just {direction_plain} — this outcome is now considered {direction_meaning}\n"
               f"Odds: {a['prev']*100:.0f}% → <b>{a['yes']*100:.0f}%</b> ({a['pct']:+.1f}%)\n"
               f"24h volume: ${a['vol']:,.0f}\n\n"
               f"👉 https://polymarket.com/event/{a['event_slug']}")
        send_paid_alert(msg, alert_type="price_move")
        # Free: teaser only — no market title, no odds, no link
        if abs(a["pct"]) >= 20:
            send_free_alert(format_scanner_teaser(a["pct"], a["cat"]), alert_type="price_move")
        time.sleep(0.5)
def startup():
    print("=" * 50)
    print("  WhalePulse Market Scanner")
    print("=" * 50)
    init_db()
    _load_state()
    log(f"Restored state: {len(_previous_prices)} prices, {len(_alerted_markets)} alerted markets")
    log("Building/refreshing baseline...")
    scan_markets()
    log(f"Baseline: {len(_previous_prices)} markets")
    send_admin_alert(f"Market Scanner started - tracking {len(_previous_prices)} markets")

if __name__ == "__main__":
    startup()
    schedule.every(SCAN_INTERVAL).minutes.do(scan_markets)
    print(f"\nScanning every {SCAN_INTERVAL}min. Ctrl+C to stop.")
    while True:
        try:
            schedule.run_pending()
            time.sleep(30)
        except KeyboardInterrupt:
            print("\nStopped.")
            break
        except Exception as e:
            print(f"\n[ERROR] {e}")
            time.sleep(60)
