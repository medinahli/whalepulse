#!/usr/bin/env python3
"""
WhalePulse Bootstrap — Run this once to write all project files.
Usage: python3 bootstrap.py
"""
import os

BASE = os.path.expanduser("~/whalepulse")

FILES = {}

# ── shared/__init__.py ──
FILES["shared/__init__.py"] = ""

# ── shared/database.py ──
FILES["shared/database.py"] = r'''"""
WhalePulse Database Layer
"""
import sqlite3
import json
import time
from pathlib import Path
from contextlib import contextmanager

DB_PATH = Path(__file__).parent.parent / "data" / "whalepulse.db"

def _ensure_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

@contextmanager
def get_db():
    _ensure_db()
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    with get_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS wallets (
            address TEXT PRIMARY KEY,
            name TEXT DEFAULT '',
            pseudonym TEXT DEFAULT '',
            tier INTEGER DEFAULT 2,
            score REAL DEFAULT 50.0,
            total_trades INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            total_pnl REAL DEFAULT 0.0,
            avg_trade_size REAL DEFAULT 0.0,
            best_category TEXT DEFAULT '',
            best_category_winrate REAL DEFAULT 0.0,
            discovered_at INTEGER DEFAULT 0,
            last_active INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            profile_image TEXT DEFAULT '',
            bio TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS trades (
            id TEXT PRIMARY KEY,
            wallet_address TEXT NOT NULL,
            side TEXT NOT NULL,
            title TEXT DEFAULT '',
            slug TEXT DEFAULT '',
            event_slug TEXT DEFAULT '',
            outcome TEXT DEFAULT '',
            outcome_index INTEGER DEFAULT 0,
            size REAL DEFAULT 0.0,
            price REAL DEFAULT 0.0,
            usdc_value REAL DEFAULT 0.0,
            timestamp INTEGER DEFAULT 0,
            tx_hash TEXT DEFAULT '',
            condition_id TEXT DEFAULT '',
            asset TEXT DEFAULT '',
            resolved INTEGER DEFAULT 0,
            won INTEGER DEFAULT 0,
            pnl REAL DEFAULT 0.0,
            alerted_free INTEGER DEFAULT 0,
            alerted_paid INTEGER DEFAULT 0,
            signal_score INTEGER DEFAULT 0,
            category TEXT DEFAULT '',
            FOREIGN KEY (wallet_address) REFERENCES wallets(address)
        );
        CREATE TABLE IF NOT EXISTS positions (
            id TEXT PRIMARY KEY,
            wallet_address TEXT NOT NULL,
            title TEXT DEFAULT '',
            slug TEXT DEFAULT '',
            event_slug TEXT DEFAULT '',
            outcome TEXT DEFAULT '',
            size REAL DEFAULT 0.0,
            avg_price REAL DEFAULT 0.0,
            current_price REAL DEFAULT 0.0,
            initial_value REAL DEFAULT 0.0,
            current_value REAL DEFAULT 0.0,
            cash_pnl REAL DEFAULT 0.0,
            percent_pnl REAL DEFAULT 0.0,
            updated_at INTEGER DEFAULT 0,
            FOREIGN KEY (wallet_address) REFERENCES wallets(address)
        );
        CREATE TABLE IF NOT EXISTS convergence_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            condition_id TEXT NOT NULL,
            title TEXT DEFAULT '',
            slug TEXT DEFAULT '',
            wallet_count INTEGER DEFAULT 0,
            wallets TEXT DEFAULT '[]',
            dominant_side TEXT DEFAULT '',
            total_size REAL DEFAULT 0.0,
            signal_score INTEGER DEFAULT 0,
            detected_at INTEGER DEFAULT 0,
            alerted INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS alert_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_type TEXT NOT NULL,
            tier TEXT DEFAULT 'free',
            content TEXT DEFAULT '',
            sent_at INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_trades_wallet ON trades(wallet_address);
        CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_trades_condition ON trades(condition_id);
        CREATE INDEX IF NOT EXISTS idx_trades_alerted ON trades(alerted_paid, alerted_free);
        CREATE INDEX IF NOT EXISTS idx_positions_wallet ON positions(wallet_address);
        CREATE INDEX IF NOT EXISTS idx_convergence_condition ON convergence_events(condition_id);
        """)

def upsert_wallet(address, **kwargs):
    with get_db() as db:
        existing = db.execute("SELECT * FROM wallets WHERE address = ?", (address,)).fetchone()
        if existing:
            sets = ", ".join(f"{k} = ?" for k in kwargs)
            vals = list(kwargs.values()) + [address]
            if sets:
                db.execute(f"UPDATE wallets SET {sets} WHERE address = ?", vals)
        else:
            kwargs["address"] = address
            kwargs.setdefault("discovered_at", int(time.time()))
            cols = ", ".join(kwargs.keys())
            placeholders = ", ".join("?" for _ in kwargs)
            db.execute(f"INSERT INTO wallets ({cols}) VALUES ({placeholders})", list(kwargs.values()))

def get_wallet(address):
    with get_db() as db:
        return db.execute("SELECT * FROM wallets WHERE address = ?", (address,)).fetchone()

def get_active_wallets(min_score=0, limit=100):
    with get_db() as db:
        return db.execute(
            "SELECT * FROM wallets WHERE is_active = 1 AND score >= ? ORDER BY score DESC LIMIT ?",
            (min_score, limit)).fetchall()

def get_top_wallets(n=10):
    with get_db() as db:
        return db.execute(
            "SELECT * FROM wallets WHERE is_active = 1 ORDER BY score DESC LIMIT ?", (n,)).fetchall()

def update_wallet_score(address, score, **extra):
    with get_db() as db:
        sets = ["score = ?"]
        vals = [score]
        for k, v in extra.items():
            sets.append(f"{k} = ?")
            vals.append(v)
        vals.append(address)
        db.execute(f"UPDATE wallets SET {', '.join(sets)} WHERE address = ?", vals)

def insert_trade(trade_data):
    with get_db() as db:
        try:
            cols = ", ".join(trade_data.keys())
            placeholders = ", ".join("?" for _ in trade_data)
            db.execute(f"INSERT OR IGNORE INTO trades ({cols}) VALUES ({placeholders})",
                       list(trade_data.values()))
            return db.execute("SELECT changes()").fetchone()[0] > 0
        except sqlite3.IntegrityError:
            return False

def get_trade(trade_id):
    with get_db() as db:
        return db.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()

def get_wallet_trades(address, limit=50, since=None):
    with get_db() as db:
        if since:
            return db.execute(
                "SELECT * FROM trades WHERE wallet_address = ? AND timestamp > ? ORDER BY timestamp DESC LIMIT ?",
                (address, since, limit)).fetchall()
        return db.execute(
            "SELECT * FROM trades WHERE wallet_address = ? ORDER BY timestamp DESC LIMIT ?",
            (address, limit)).fetchall()

def get_unalerted_trades(tier="paid"):
    col = f"alerted_{tier}"
    with get_db() as db:
        return db.execute(
            f"SELECT t.*, w.name, w.score, w.wins, w.losses, w.total_trades as w_total "
            f"FROM trades t JOIN wallets w ON t.wallet_address = w.address "
            f"WHERE t.{col} = 0 ORDER BY t.signal_score DESC, t.timestamp DESC").fetchall()

def mark_trade_alerted(trade_id, tier="paid"):
    col = f"alerted_{tier}"
    with get_db() as db:
        db.execute(f"UPDATE trades SET {col} = 1 WHERE id = ?", (trade_id,))

def get_recent_trades_for_condition(condition_id, hours=6):
    cutoff = int(time.time()) - (hours * 3600)
    with get_db() as db:
        return db.execute(
            "SELECT t.*, w.name, w.score FROM trades t "
            "JOIN wallets w ON t.wallet_address = w.address "
            "WHERE t.condition_id = ? AND t.timestamp > ? ORDER BY t.timestamp DESC",
            (condition_id, cutoff)).fetchall()

def upsert_position(pos_data):
    with get_db() as db:
        db.execute(
            "INSERT OR REPLACE INTO positions ({}) VALUES ({})".format(
                ", ".join(pos_data.keys()), ", ".join("?" for _ in pos_data)),
            list(pos_data.values()))

def get_wallet_positions(address):
    with get_db() as db:
        return db.execute(
            "SELECT * FROM positions WHERE wallet_address = ? ORDER BY current_value DESC",
            (address,)).fetchall()

def insert_convergence(event_data):
    with get_db() as db:
        db.execute(
            "INSERT INTO convergence_events (condition_id, title, slug, wallet_count, wallets, "
            "dominant_side, total_size, signal_score, detected_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (event_data["condition_id"], event_data["title"], event_data["slug"],
             event_data["wallet_count"], json.dumps(event_data["wallets"]),
             event_data["dominant_side"], event_data["total_size"],
             event_data["signal_score"], int(time.time())))

def get_unalerted_convergences():
    with get_db() as db:
        return db.execute(
            "SELECT * FROM convergence_events WHERE alerted = 0 ORDER BY signal_score DESC").fetchall()

def mark_convergence_alerted(cid):
    with get_db() as db:
        db.execute("UPDATE convergence_events SET alerted = 1 WHERE id = ?", (cid,))

def get_stats():
    with get_db() as db:
        wallets = db.execute("SELECT COUNT(*) FROM wallets WHERE is_active = 1").fetchone()[0]
        trades = db.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        trades_24h = db.execute(
            "SELECT COUNT(*) FROM trades WHERE timestamp > ?", (int(time.time()) - 86400,)).fetchone()[0]
        alerts = db.execute(
            "SELECT COUNT(*) FROM alert_log WHERE sent_at > ?", (int(time.time()) - 86400,)).fetchone()[0]
        return {"active_wallets": wallets, "total_trades": trades,
                "trades_24h": trades_24h, "alerts_24h": alerts}

def log_alert(alert_type, tier, content):
    with get_db() as db:
        db.execute(
            "INSERT INTO alert_log (alert_type, tier, content, sent_at) VALUES (?, ?, ?, ?)",
            (alert_type, tier, content[:500], int(time.time())))
'''

# ── shared/ai_client.py ──
FILES["shared/ai_client.py"] = r'''"""
WhalePulse AI Client
"""
import os
import json
import hashlib
import time
import anthropic
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / "config" / ".env")

_client = None
CACHE_DIR = Path(__file__).parent.parent / "data" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
COST_LOG = Path(__file__).parent.parent / "data" / "api_costs.json"
_call_timestamps = []
MAX_CALLS_PER_MIN = 15

def _get_client():
    global _client
    if _client is None:
        key = os.getenv("ANTHROPIC_API_KEY")
        if not key or key == "your-key-here":
            return None
        _client = anthropic.Anthropic(api_key=key)
    return _client

def _rate_limit():
    now = time.time()
    _call_timestamps[:] = [t for t in _call_timestamps if now - t < 60]
    if len(_call_timestamps) >= MAX_CALLS_PER_MIN:
        sleep_time = 60 - (now - _call_timestamps[0])
        if sleep_time > 0:
            time.sleep(sleep_time)
    _call_timestamps.append(time.time())

def _log_cost(input_tokens, output_tokens, model):
    cost = (input_tokens * 3 / 1_000_000) + (output_tokens * 15 / 1_000_000)
    try:
        if COST_LOG.exists():
            data = json.loads(COST_LOG.read_text())
        else:
            data = {"total_cost": 0, "total_calls": 0, "daily": {}}
        data["total_cost"] += cost
        data["total_calls"] += 1
        today = time.strftime("%Y-%m-%d")
        if today not in data["daily"]:
            data["daily"][today] = {"cost": 0, "calls": 0}
        data["daily"][today]["cost"] += cost
        data["daily"][today]["calls"] += 1
        COST_LOG.write_text(json.dumps(data, indent=2))
    except Exception:
        pass
    return cost

def ask_ai(prompt, system="You are a helpful assistant.", use_cache=True,
           max_tokens=1024, cache_ttl=3600):
    client = _get_client()
    if not client:
        return "[AI unavailable]"
    key = hashlib.md5(f"{system}|{prompt}".encode()).hexdigest()
    cache_file = CACHE_DIR / f"{key}.json"
    if use_cache and cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text())
            if time.time() - cached.get("timestamp", 0) < cache_ttl:
                return cached["response"]
        except (json.JSONDecodeError, KeyError):
            pass
    _rate_limit()
    try:
        message = client.messages.create(
            model="claude-sonnet-4-20250514", max_tokens=max_tokens,
            system=system, messages=[{"role": "user", "content": prompt}])
        response = message.content[0].text
        _log_cost(message.usage.input_tokens, message.usage.output_tokens, "sonnet")
        if use_cache:
            cache_file.write_text(json.dumps({"response": response, "timestamp": time.time()}))
        return response
    except Exception as e:
        return f"[AI error: {e}]"

def get_cost_summary():
    try:
        if COST_LOG.exists():
            data = json.loads(COST_LOG.read_text())
            today = time.strftime("%Y-%m-%d")
            daily = data.get("daily", {}).get(today, {"cost": 0, "calls": 0})
            return {"total_cost": round(data.get("total_cost", 0), 4),
                    "total_calls": data.get("total_calls", 0),
                    "today_cost": round(daily["cost"], 4),
                    "today_calls": daily["calls"]}
    except Exception:
        pass
    return {"total_cost": 0, "total_calls": 0, "today_cost": 0, "today_calls": 0}
'''

# ── shared/polymarket_api.py ──
FILES["shared/polymarket_api.py"] = r'''"""
WhalePulse Polymarket API
"""
import time
import httpx

DATA_API = "https://data-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
_last_call = 0
MIN_INTERVAL = 0.3

def _throttle():
    global _last_call
    elapsed = time.time() - _last_call
    if elapsed < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - elapsed)
    _last_call = time.time()

def _get(url, params=None, timeout=30):
    _throttle()
    try:
        resp = httpx.get(url, params=params, timeout=timeout, headers={"Accept": "application/json"})
        if resp.status_code == 200:
            return resp.json()
        return None
    except Exception as e:
        print(f"  [API ERROR] {url}: {e}")
        return None

def get_wallet_activity(address, limit=100, trade_type="TRADE", since=None):
    params = {"user": address, "limit": limit}
    if trade_type:
        params["type"] = trade_type
    if since:
        params["start"] = since
    params["sortBy"] = "TIMESTAMP"
    params["sortDirection"] = "DESC"
    result = _get(f"{DATA_API}/activity", params)
    return result if isinstance(result, list) else []

def get_wallet_positions(address, sort_by="CURRENT", limit=50):
    params = {"user": address, "sortBy": sort_by, "sortDirection": "DESC",
              "limit": limit, "sizeThreshold": 0.1}
    result = _get(f"{DATA_API}/positions", params)
    return result if isinstance(result, list) else []

def get_markets(active=True, closed=False, limit=100, cursor=None):
    params = {"limit": limit}
    if active: params["active"] = "true"
    if not closed: params["closed"] = "false"
    if cursor: params["next_cursor"] = cursor
    result = _get(f"{CLOB_API}/markets", params)
    if isinstance(result, dict):
        return result.get("data", []), result.get("next_cursor")
    elif isinstance(result, list):
        return result, None
    return [], None

def get_all_active_markets(max_pages=10):
    all_markets = []
    cursor = None
    for _ in range(max_pages):
        markets, cursor = get_markets(cursor=cursor)
        all_markets.extend(markets)
        if not cursor or cursor == "LTE=": break
    return all_markets

def get_gamma_markets(limit=50, active=True, order="volume24hr", ascending=False, tag_slug=None):
    params = {"limit": limit, "order": order, "ascending": str(ascending).lower()}
    if active:
        params["active"] = "true"
        params["closed"] = "false"
    if tag_slug: params["tag_slug"] = tag_slug
    result = _get(f"{GAMMA_API}/markets", params)
    return result if isinstance(result, list) else []

def get_gamma_events(limit=20, active=True, order="volume24hr"):
    params = {"limit": limit, "order": order, "ascending": "false"}
    if active:
        params["active"] = "true"
        params["closed"] = "false"
    result = _get(f"{GAMMA_API}/events", params)
    return result if isinstance(result, list) else []

def classify_market_category(title, tags=None):
    title_lower = (title or "").lower()
    categories = {
        "politics": ["election", "president", "congress", "senate", "democrat",
                     "republican", "trump", "biden", "vote", "governor", "political"],
        "crypto": ["bitcoin", "btc", "ethereum", "eth", "crypto", "solana", "token", "blockchain"],
        "sports": ["nba", "nfl", "mlb", "nhl", "soccer", "football", "basketball",
                   "baseball", "tennis", "ufc", "mma", "championship", "super bowl"],
        "economy": ["fed", "interest rate", "inflation", "gdp", "recession",
                    "unemployment", "stock", "s&p", "nasdaq"],
        "tech": ["ai ", "openai", "google", "apple", "microsoft", "meta", "tesla", "spacex"],
        "world": ["war", "ceasefire", "sanctions", "nato", "china", "russia", "ukraine"],
        "culture": ["oscar", "grammy", "emmy", "movie", "album", "celebrity"],
    }
    for cat, keywords in categories.items():
        for kw in keywords:
            if kw in title_lower:
                return cat
    return "other"

def extract_wallet_info(activity_item):
    return {
        "address": activity_item.get("proxyWallet", ""),
        "name": activity_item.get("name", ""),
        "pseudonym": activity_item.get("pseudonym", ""),
        "profile_image": activity_item.get("profileImage", ""),
        "bio": activity_item.get("bio", ""),
    }
'''

# ── shared/notifier.py ──
FILES["shared/notifier.py"] = r'''"""
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
PAID_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
FREE_CHAT_ID = os.getenv("TELEGRAM_FREE_CHANNEL_ID")
MAX_MSG_LEN = 4000

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
        resp2 = httpx.post(url, json={
            "chat_id": chat_id,
            "text": text.replace("<b>","").replace("</b>","").replace("<i>","").replace("</i>","").replace("<code>","").replace("</code>",""),
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
    return _send_telegram(PAID_CHAT_ID, text, silent=silent)

def _signal_bar(score):
    filled = min(score // 10, 10)
    return "\u2588" * filled + "\u2591" * (10 - filled)

def format_whale_trade(trade, wallet, signal_score=0):
    side_emoji = "\U0001f7e2" if trade["side"] == "BUY" else "\U0001f534"
    side_label = trade["side"]
    avg_size = wallet.get("avg_trade_size", 0) or 1
    usdc_val = trade.get("usdc_value", 0) or (trade["size"] * trade["price"])
    ratio = usdc_val / avg_size if avg_size > 0 else 1
    if ratio >= 3: conviction = "\U0001f525\U0001f525\U0001f525 VERY HIGH"
    elif ratio >= 2: conviction = "\U0001f525\U0001f525 HIGH"
    elif ratio >= 1.2: conviction = "\U0001f525 ABOVE AVG"
    else: conviction = "NORMAL"
    w_total = wallet.get("total_trades", 0) or wallet.get("w_total", 0)
    wins = wallet.get("wins", 0)
    winrate = f"{(wins / w_total * 100):.0f}%" if w_total > 5 else "NEW"
    name = wallet.get("name") or wallet.get("pseudonym") or trade["wallet_address"][:10]
    price_pct = f"{trade['price'] * 100:.0f}%"
    title = trade.get("title", "Unknown")[:120]
    outcome = trade.get("outcome", "Yes")
    score_bar = _signal_bar(signal_score)
    return (
        f"{side_emoji} <b>WHALE TRADE</b>\n\n"
        f"<b>{name}</b> \u2014 Score: {wallet.get('score', 50):.0f}/100 | WR: {winrate}\n\n"
        f"{side_label} <b>{outcome}</b> @ {price_pct}\n"
        f"Size: {trade['size']:,.0f} shares | ${usdc_val:,.0f}\n"
        f"Conviction: {conviction}\n\n"
        f"<i>{title}</i>\n\n"
        f"Signal: {score_bar} ({signal_score}/100)\n"
        f"https://polymarket.com/event/{trade.get('event_slug', trade.get('slug', ''))}")

def format_convergence_alert(event):
    wallet_names = event.get("wallet_names", [])
    names_str = ", ".join(wallet_names[:5])
    if len(wallet_names) > 5: names_str += f" +{len(wallet_names) - 5} more"
    score_bar = _signal_bar(event["signal_score"])
    return (
        f"\U0001f40b\U0001f40b\U0001f40b <b>CONVERGENCE ALERT</b>\n\n"
        f"<b>{event['wallet_count']} whales</b> betting {event['dominant_side']} on:\n"
        f"<i>{event['title'][:120]}</i>\n\n"
        f"Traders: {names_str}\nCombined: ${event['total_size']:,.0f}\n\n"
        f"Signal: {score_bar} ({event['signal_score']}/100)\n"
        f"https://polymarket.com/event/{event.get('slug', '')}")

def format_daily_digest(wallets, trades_24h, convergences, ai_summary=""):
    msg = f"\U0001f4ca <b>WHALEPULSE DAILY DIGEST</b>\n{'=' * 28}\n\n"
    msg += "<b>\U0001f3c6 Top Wallets by Score</b>\n"
    for i, w in enumerate(wallets[:5], 1):
        wt = w["total_trades"] or 0
        wr = f"{(w['wins'] / wt * 100):.0f}%" if wt > 5 else "NEW"
        msg += f"  {i}. <b>{w['name'] or w['address'][:8]}</b> \u2014 {w['score']:.0f}pts | WR: {wr}\n"
    msg += f"\n<b>\U0001f4c8 Last 24h</b>\n  Trades tracked: {trades_24h}\n  Convergence signals: {convergences}\n"
    if ai_summary: msg += f"\n<b>\U0001f916 AI Analysis</b>\n{ai_summary[:800]}\n"
    msg += f"\n<i>WhalePulse \u2014 smartest whale tracker on Polymarket</i>"
    return msg

def format_wallet_report(wallet, recent_trades, positions):
    w = wallet
    name = w["name"] or w["address"][:12]
    wt = w["total_trades"] or 0
    wr = f"{(w['wins'] / wt * 100):.0f}%" if wt > 5 else "NEW"
    msg = (f"\U0001f464 <b>WHALE REPORT: {name}</b>\n{'-' * 28}\n"
           f"Score: <b>{w['score']:.0f}/100</b> | Win Rate: {wr}\n"
           f"Trades: {wt} | PnL: ${w['total_pnl']:,.0f}\nAvg Trade: ${w['avg_trade_size']:,.0f}\n")
    if w.get("best_category"):
        msg += f"Best at: {w['best_category']} ({w['best_category_winrate']:.0f}%)\n"
    if positions:
        msg += f"\n<b>Open Positions ({len(positions)})</b>\n"
        for p in positions[:5]:
            emoji = "\U0001f4c8" if (p.get("cash_pnl") or 0) > 0 else "\U0001f4c9"
            msg += f"  {emoji} {p['title'][:40]} \u2014 ${p.get('current_value', 0):,.0f} ({p.get('percent_pnl', 0):+.0f}%)\n"
    if recent_trades:
        msg += f"\n<b>Recent Trades</b>\n"
        for t in recent_trades[:5]:
            se = "\U0001f7e2" if t["side"] == "BUY" else "\U0001f534"
            msg += f"  {se} {t['side']} {t.get('outcome', '')} @ {t['price']*100:.0f}% \u2014 ${t['size']*t['price']:,.0f}\n"
            msg += f"     <i>{t['title'][:50]}</i>\n"
    return msg
'''

# ── bots/__init__.py ──
FILES["bots/__init__.py"] = ""
FILES["bots/whale_tracker/__init__.py"] = ""
FILES["bots/scanner/__init__.py"] = ""
FILES["bots/admin/__init__.py"] = ""

# ── bots/whale_tracker/main.py ──
FILES["bots/whale_tracker/main.py"] = r'''#!/usr/bin/env python3
"""
WhalePulse Whale Tracker — Core Engine
"""
import sys, time, json, schedule, traceback
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from shared.database import (
    init_db, upsert_wallet, get_wallet, get_active_wallets, get_top_wallets,
    update_wallet_score, insert_trade, get_trade, get_wallet_trades,
    get_unalerted_trades, mark_trade_alerted, get_recent_trades_for_condition,
    upsert_position, get_wallet_positions as db_get_positions,
    insert_convergence, get_unalerted_convergences, mark_convergence_alerted,
    get_stats, log_alert)
from shared.polymarket_api import (
    get_wallet_activity, get_wallet_positions, get_gamma_markets,
    classify_market_category, extract_wallet_info)
from shared.notifier import (
    send_paid_alert, send_free_alert, send_admin_alert,
    format_whale_trade, format_convergence_alert, format_daily_digest)
from shared.ai_client import ask_ai, get_cost_summary

SEED_WALLETS = [
    {"address": "0x6af75d4e4aaf700450efbac3708cce1665810ff1", "name": "gopfan", "tier": 1},
    {"address": "0xd91cfba90a0964e90a9e1f65394024e0f8a8aa03", "name": "Theo", "tier": 1},
    {"address": "0x1b7b3febbc86cebc769c5a1c0e3b6e3170e8d1a0", "name": "Fredi9999", "tier": 1},
    {"address": "0x72bc62f2b5a1a9bff1ae6b4e28baacc648e2f364", "name": "SilverBera", "tier": 1},
    {"address": "0x58c953c0e7e6123d7bce2ae2d52adbc7e8923358", "name": "JLin", "tier": 1},
    {"address": "0x4871309843e5e7b45b52c7f0cad10d3e8b28f875", "name": "PredictoorAce", "tier": 2},
    {"address": "0x87e27edee31a104afbb9f17ebad64e67eaa4b1b0", "name": "BigWhale", "tier": 2},
    {"address": "0x3b37b293acab7a45d5e5a0f88cbe60d7ff695a45", "name": "CryptoOracle", "tier": 2},
    {"address": "0x1503ee6f1e882543fd13dd59a91e8e6e24ab2dbb", "name": "PoliticalEdge", "tier": 2},
    {"address": "0xea1a5037e22e5b2bb9facb7a1ee7d0b7d5f5c5f9", "name": "DataDriven", "tier": 2},
]

MIN_SIGNAL_PAID = 30
MIN_SIGNAL_FREE = 60
MIN_TRADE_VALUE = 500
TRADE_CHECK_INTERVAL = 3
DISCOVERY_INTERVAL = 6
SCORING_INTERVAL = 4
DIGEST_TIME = "09:00"
FREE_DELAY = 1800

def log(msg):
    print(f"  {msg}")

def check_whale_trades():
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n[{ts}] Checking whale trades...")
    wallets = get_active_wallets(limit=50)
    if not wallets:
        log("No active wallets. Seeding...")
        seed_wallets()
        wallets = get_active_wallets(limit=50)
    new_trades_total = 0
    active_count = 0
    for w in wallets:
        try:
            since = int(time.time()) - 600
            trades = get_wallet_activity(w["address"], limit=20, since=since)
            if not trades: continue
            active_count += 1
            new_for_wallet = 0
            for t in trades:
                trade_data = _process_trade(t, w)
                if trade_data and insert_trade(trade_data):
                    new_for_wallet += 1
                    new_trades_total += 1
                    info = extract_wallet_info(t)
                    if info.get("name"):
                        upsert_wallet(w["address"], name=info["name"],
                                      pseudonym=info.get("pseudonym", ""),
                                      profile_image=info.get("profile_image", ""),
                                      last_active=int(time.time()))
            if new_for_wallet > 0:
                log(f"  {w['name'] or w['address'][:10]}: {new_for_wallet} new trades")
        except Exception as e:
            log(f"  Error checking {w['address'][:10]}: {e}")
    log(f"Checked {len(wallets)} wallets | Active: {active_count} | New trades: {new_trades_total}")
    if new_trades_total > 0:
        _send_trade_alerts()
        _check_convergence()

def _process_trade(raw_trade, wallet):
    addr = raw_trade.get("proxyWallet", "")
    tx_hash = raw_trade.get("transactionHash", "")
    if not tx_hash: return None
    trade_id = f"{addr[:10]}_{tx_hash[:16]}_{raw_trade.get('timestamp', 0)}"
    if get_trade(trade_id): return None
    size = float(raw_trade.get("size", 0) or 0)
    price = float(raw_trade.get("price", 0) or 0)
    usdc_value = size * price
    if usdc_value < MIN_TRADE_VALUE: return None
    title = raw_trade.get("title", "")
    category = classify_market_category(title)
    signal = _calculate_signal_score(raw_trade, wallet, usdc_value, category)
    return {
        "id": trade_id, "wallet_address": addr, "side": raw_trade.get("side", ""),
        "title": title, "slug": raw_trade.get("slug", ""),
        "event_slug": raw_trade.get("eventSlug", ""),
        "outcome": raw_trade.get("outcome", ""),
        "outcome_index": raw_trade.get("outcomeIndex", 0),
        "size": size, "price": price, "usdc_value": usdc_value,
        "timestamp": raw_trade.get("timestamp", int(time.time())),
        "tx_hash": tx_hash, "condition_id": raw_trade.get("conditionId", ""),
        "asset": raw_trade.get("asset", ""), "resolved": 0, "won": 0, "pnl": 0.0,
        "alerted_free": 0, "alerted_paid": 0, "signal_score": signal, "category": category,
    }

def _calculate_signal_score(trade, wallet, usdc_value, category):
    score = 0
    wallet_score = wallet.get("score", 50) if isinstance(wallet, dict) else 50
    score += min(35, wallet_score * 0.35)
    avg_size = wallet.get("avg_trade_size", 0) if isinstance(wallet, dict) else 0
    if avg_size > 0 and usdc_value > 0:
        r = usdc_value / avg_size
        if r >= 5: score += 25
        elif r >= 3: score += 20
        elif r >= 2: score += 15
        elif r >= 1: score += 10
        else: score += 5
    elif usdc_value >= 10000: score += 20
    elif usdc_value >= 5000: score += 15
    elif usdc_value >= 1000: score += 10
    w_total = wallet.get("total_trades", 0) if isinstance(wallet, dict) else 0
    wins = wallet.get("wins", 0) if isinstance(wallet, dict) else 0
    if w_total > 10: score += min(20, int(wins / w_total * 25))
    elif w_total > 5: score += min(15, int(wins / w_total * 20))
    tier = wallet.get("tier", 2) if isinstance(wallet, dict) else 2
    score += 10 if tier == 1 else 5
    price = float(trade.get("price", 0.5))
    side = trade.get("side", "BUY")
    if side == "BUY" and price < 0.15: score += 10
    elif side == "BUY" and price > 0.85: score += 8
    elif side == "BUY" and price < 0.30: score += 7
    elif side == "SELL" and price > 0.85: score += 8
    return min(100, max(0, int(score)))

def _send_trade_alerts():
    paid_trades = get_unalerted_trades("paid")
    for t in paid_trades:
        if t["signal_score"] >= MIN_SIGNAL_PAID:
            wi = {"name": t.get("name",""), "score": t.get("score",50),
                  "total_trades": t.get("w_total",0), "wins": t.get("wins",0), "avg_trade_size": 0}
            w = get_wallet(t["wallet_address"])
            if w: wi["avg_trade_size"] = w["avg_trade_size"]
            msg = format_whale_trade(dict(t), wi, t["signal_score"])
            send_paid_alert(msg)
            time.sleep(0.5)
        mark_trade_alerted(t["id"], "paid")
    free_trades = get_unalerted_trades("free")
    for t in free_trades:
        age = int(time.time()) - t["timestamp"]
        if age >= FREE_DELAY and t["signal_score"] >= MIN_SIGNAL_FREE:
            wi = {"name": t.get("name",""), "score": t.get("score",50),
                  "total_trades": t.get("w_total",0), "wins": t.get("wins",0), "avg_trade_size": 0}
            msg = format_whale_trade(dict(t), wi, t["signal_score"])
            msg += "\n\n<i>Get real-time alerts: upgrade to Pro</i>"
            send_free_alert(msg)
            time.sleep(0.5)
        if age >= FREE_DELAY:
            mark_trade_alerted(t["id"], "free")

def _check_convergence():
    from shared.database import get_db
    cutoff = int(time.time()) - (6 * 3600)
    with get_db() as db:
        rows = db.execute(
            "SELECT t.condition_id, t.title, t.slug, t.event_slug, t.side, "
            "t.wallet_address, t.usdc_value, w.name, w.score "
            "FROM trades t JOIN wallets w ON t.wallet_address = w.address "
            "WHERE t.timestamp > ? AND t.signal_score >= 20 ORDER BY t.condition_id",
            (cutoff,)).fetchall()
    by_market = defaultdict(list)
    for r in rows: by_market[r["condition_id"]].append(dict(r))
    for cond_id, trades in by_market.items():
        unique = set(t["wallet_address"] for t in trades)
        if len(unique) < 2: continue
        buys = sum(1 for t in trades if t["side"] == "BUY")
        dom = "BUY" if buys >= len(trades) - buys else "SELL"
        agree = max(buys, len(trades) - buys) / len(trades)
        if agree < 0.6: continue
        total_size = sum(t["usdc_value"] for t in trades)
        names = list(set(t["name"] or t["wallet_address"][:8] for t in trades))
        avg_sc = sum(t["score"] for t in trades) / len(trades)
        sig = min(100, int(len(unique)*15 + agree*20 + min(30, total_size/1000) + avg_sc*0.2))
        if sig >= 40:
            insert_convergence({
                "condition_id": cond_id, "title": trades[0]["title"],
                "slug": trades[0].get("event_slug") or trades[0].get("slug", ""),
                "wallet_count": len(unique), "wallets": list(unique),
                "wallet_names": names, "dominant_side": dom,
                "total_size": total_size, "signal_score": sig})
    for c in get_unalerted_convergences():
        cd = dict(c)
        cd["wallet_names"] = json.loads(cd.get("wallets", "[]"))
        msg = format_convergence_alert(cd)
        send_paid_alert(msg, alert_type="convergence")
        if cd.get("signal_score", 0) >= 70:
            send_free_alert(msg + "\n\n<i>Get all convergence alerts: upgrade to Pro</i>", alert_type="convergence")
        mark_convergence_alerted(c["id"])
        time.sleep(0.5)

def discover_whales():
    print(f"\n[{datetime.now(timezone.utc).strftime('%H:%M UTC')}] Discovering whales...")
    from shared.database import get_db
    with get_db() as db:
        existing = set(r[0] for r in db.execute("SELECT address FROM wallets").fetchall())
    found = 0
    for seed in SEED_WALLETS:
        if seed["address"] in existing: continue
        try:
            activity = get_wallet_activity(seed["address"], limit=5)
            if activity:
                info = extract_wallet_info(activity[0])
                upsert_wallet(seed["address"], name=seed.get("name") or info.get("name",""),
                              pseudonym=info.get("pseudonym",""), tier=seed.get("tier",2),
                              score=50.0, profile_image=info.get("profile_image",""))
                found += 1
        except Exception: continue
    if found: log(f"Seeded {found} wallets")

def update_scores():
    print(f"\n[{datetime.now(timezone.utc).strftime('%H:%M UTC')}] Updating scores...")
    wallets = get_active_wallets(min_score=0, limit=200)
    updated = 0
    for w in wallets:
        try:
            positions = get_wallet_positions(w["address"], limit=50)
            total_pnl = sum(float(p.get("cashPnl",0) or 0) for p in positions)
            total_initial = sum(float(p.get("initialValue",0) or 0) for p in positions)
            wins = sum(1 for p in positions if float(p.get("cashPnl",0) or 0) > 0)
            losses = sum(1 for p in positions if float(p.get("cashPnl",0) or 0) < 0)
            recent = get_wallet_trades(w["address"], limit=50)
            trade_count = len(recent)
            avg_size = sum(t["usdc_value"] for t in recent) / trade_count if trade_count else 0
            score = 50
            if total_initial > 0:
                roi = total_pnl / total_initial
                score = score - 17.5 + min(35, max(0, 17.5 + roi * 35))
            total_resolved = wins + losses
            if total_resolved > 5:
                score = score - 15 + min(30, max(0, (wins/total_resolved) * 40))
            days_inactive = (time.time() - (w["last_active"] or w["discovered_at"])) / 86400
            if days_inactive < 1: score += 10
            elif days_inactive < 7: score += 5
            elif days_inactive > 30: score -= 10
            if trade_count > 20: score += 5
            elif trade_count > 5: score += 3
            score = min(100, max(0, score))
            # Best category
            cats = defaultdict(lambda: {"w":0,"t":0})
            for p in positions:
                c = classify_market_category(p.get("title",""))
                cats[c]["t"] += 1
                if float(p.get("percentPnl",0) or 0) > 0: cats[c]["w"] += 1
            best_cat, best_wr = "", 0
            for c, d in cats.items():
                if d["t"] >= 3:
                    wr = d["w"]/d["t"]
                    if wr > best_wr: best_cat, best_wr = c, wr
            update_wallet_score(w["address"], round(score,1),
                total_trades=max(w["total_trades"], trade_count), wins=wins, losses=losses,
                total_pnl=round(total_pnl,2), avg_trade_size=round(avg_size,2),
                best_category=best_cat, best_category_winrate=round(best_wr*100,1))
            updated += 1
        except Exception as e:
            log(f"  Error scoring {w['address'][:10]}: {e}")
    log(f"Updated {updated} wallets")

def send_daily_digest():
    print(f"\n[{datetime.now(timezone.utc).strftime('%H:%M UTC')}] Daily digest...")
    top = get_top_wallets(10)
    stats = get_stats()
    ai_summary = ""
    try:
        wd = [{"name": w["name"] or w["address"][:10], "score": w["score"],
               "pnl": w["total_pnl"]} for w in top[:5]]
        ai_summary = ask_ai(
            f"Brief daily digest (4 sentences). Top whales: {json.dumps(wd)}. "
            f"Stats: {stats['trades_24h']} trades 24h. What's smart money doing?",
            system="Concise prediction market analyst.", use_cache=False, max_tokens=300)
    except Exception as e:
        ai_summary = f"AI unavailable: {e}"
    from shared.database import get_db
    with get_db() as db:
        conv = db.execute("SELECT COUNT(*) FROM convergence_events WHERE detected_at > ?",
                          (int(time.time())-86400,)).fetchone()[0]
    msg = format_daily_digest([dict(w) for w in top], stats["trades_24h"], conv, ai_summary)
    send_paid_alert(msg, alert_type="digest")
    costs = get_cost_summary()
    send_admin_alert(f"API costs today: ${costs['today_cost']:.4f} ({costs['today_calls']} calls)", silent=True)

def seed_wallets():
    for s in SEED_WALLETS:
        upsert_wallet(s["address"], name=s["name"], tier=s["tier"],
                      score=60.0 if s["tier"]==1 else 45.0)
    log(f"Seeded {len(SEED_WALLETS)} wallets")

def startup():
    print("=" * 50)
    print("  WhalePulse Whale Tracker")
    print("=" * 50)
    init_db()
    seed_wallets()
    log("Initial trade check...")
    check_whale_trades()
    log("Initial score update...")
    update_scores()
    stats = get_stats()
    send_admin_alert(
        f"WhalePulse Started\nTracking {stats['active_wallets']} wallets\n"
        f"DB: {stats['total_trades']} trades\nChecking every {TRADE_CHECK_INTERVAL} min")

if __name__ == "__main__":
    startup()
    schedule.every(TRADE_CHECK_INTERVAL).minutes.do(check_whale_trades)
    schedule.every(DISCOVERY_INTERVAL).hours.do(discover_whales)
    schedule.every(SCORING_INTERVAL).hours.do(update_scores)
    schedule.every().day.at(DIGEST_TIME).do(send_daily_digest)
    print(f"\nTrade checks: every {TRADE_CHECK_INTERVAL}min | Discovery: every {DISCOVERY_INTERVAL}h")
    print(f"Scores: every {SCORING_INTERVAL}h | Digest: {DIGEST_TIME} UTC")
    print("Running... Ctrl+C to stop.")
    while True:
        try:
            schedule.run_pending()
            time.sleep(30)
        except KeyboardInterrupt:
            print("\nStopped.")
            break
        except Exception as e:
            print(f"\n[ERROR] {e}")
            traceback.print_exc()
            time.sleep(60)
'''

# ── bots/scanner/main.py ──
FILES["bots/scanner/main.py"] = r'''#!/usr/bin/env python3
"""
WhalePulse Market Scanner
"""
import sys, time, json, schedule
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from shared.polymarket_api import get_gamma_markets, classify_market_category
from shared.notifier import send_paid_alert, send_free_alert, send_admin_alert
from shared.database import init_db

_previous_prices = {}
_alerted_markets = set()
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
    log(f"Tracked {tracked} markets | Alerts: {len(alerts)}")
    for a in alerts:
        msg = (f"{a['direction']} <b>PRICE MOVE</b>\n\n<i>{a['title'][:120]}</i>\n\n"
               f"YES: {a['prev']*100:.0f}% -> <b>{a['yes']*100:.0f}%</b> ({a['pct']:+.1f}%)\n"
               f"24h Volume: ${a['vol']:,.0f}\nCategory: {a['cat']}\n\n"
               f"https://polymarket.com/event/{a['event_slug']}")
        send_paid_alert(msg, alert_type="price_move")
        if abs(a["pct"]) >= 20:
            send_free_alert(msg + "\n\n<i>Get all alerts: upgrade to Pro</i>", alert_type="price_move")
        time.sleep(0.5)
    if len(_alerted_markets) > 200: _alerted_markets.clear()

def startup():
    print("=" * 50)
    print("  WhalePulse Market Scanner")
    print("=" * 50)
    init_db()
    log("Building baseline...")
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
'''

# ── bots/admin/main.py ──
FILES["bots/admin/main.py"] = r'''#!/usr/bin/env python3
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

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
'''

# ═══════════════════════════════════════════════
# Write all files
# ═══════════════════════════════════════════════

def main():
    print("WhalePulse Bootstrap")
    print("=" * 40)

    written = 0
    for relpath, content in FILES.items():
        fullpath = os.path.join(BASE, relpath)
        os.makedirs(os.path.dirname(fullpath), exist_ok=True)
        with open(fullpath, "w") as f:
            f.write(content)
        written += 1
        print(f"  OK {relpath}")

    print(f"\nWrote {written} files to {BASE}/")
    print("\nNow run:")
    print(f"  cd {BASE}")
    print(f"  PYTHONPATH={BASE} {BASE}/venv/bin/python -c \"from shared.database import init_db; init_db(); print('DB OK')\"")
    print(f"  PYTHONPATH={BASE} {BASE}/venv/bin/python bots/whale_tracker/main.py")

if __name__ == "__main__":
    main()
