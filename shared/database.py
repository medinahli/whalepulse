"""
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

class _Row(dict):
    """Dict that also supports integer index access and .get() with defaults."""
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


def _dict_row_factory(cursor, row):
    return _Row((col[0], row[idx]) for idx, col in enumerate(cursor.description))


@contextmanager
def get_db():
    _ensure_db()
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = _dict_row_factory
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
            wallet_names TEXT DEFAULT '[]',
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
        CREATE TABLE IF NOT EXISTS watchlists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            keyword TEXT NOT NULL,
            created_at INTEGER DEFAULT 0,
            UNIQUE(telegram_id, keyword)
        );
        CREATE INDEX IF NOT EXISTS idx_trades_wallet ON trades(wallet_address);
        CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_trades_condition ON trades(condition_id);
        CREATE INDEX IF NOT EXISTS idx_trades_alerted ON trades(alerted_paid, alerted_free);
        CREATE INDEX IF NOT EXISTS idx_positions_wallet ON positions(wallet_address);
        CREATE INDEX IF NOT EXISTS idx_convergence_condition ON convergence_events(condition_id);
        CREATE INDEX IF NOT EXISTS idx_watchlist_user ON watchlists(telegram_id);
        """)
        # Migrate existing tables
        try:
            db.execute("ALTER TABLE convergence_events ADD COLUMN wallet_names TEXT DEFAULT '[]'")
        except Exception:
            pass

# ── Wallets ──────────────────────────────────────────────────────────────────

_WALLET_COLUMNS = frozenset({
    "name", "pseudonym", "tier", "score", "total_trades", "wins", "losses",
    "total_pnl", "avg_trade_size", "best_category", "best_category_winrate",
    "discovered_at", "last_active", "is_active", "profile_image", "bio",
})


def upsert_wallet(address, **kwargs):
    bad = set(kwargs) - _WALLET_COLUMNS
    if bad:
        raise ValueError(f"upsert_wallet: unknown column(s): {bad}")
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
    bad = set(extra) - _WALLET_COLUMNS
    if bad:
        raise ValueError(f"update_wallet_score: unknown column(s): {bad}")
    with get_db() as db:
        sets = ["score = ?"]
        vals = [score]
        for k, v in extra.items():
            sets.append(f"{k} = ?")
            vals.append(v)
        vals.append(address)
        db.execute(f"UPDATE wallets SET {', '.join(sets)} WHERE address = ?", vals)

# ── Trades ───────────────────────────────────────────────────────────────────

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

def get_wallet_trade_count(address):
    with get_db() as db:
        return db.execute(
            "SELECT COUNT(*) FROM trades WHERE wallet_address = ?", (address,)).fetchone()[0]

def get_unalerted_trades(tier="paid"):
    col = f"alerted_{tier}"
    with get_db() as db:
        return db.execute(
            f"SELECT t.*, w.name, w.score, w.wins, w.losses, w.total_trades as w_total "
            f"FROM trades t JOIN wallets w ON t.wallet_address = w.address "
            f"WHERE t.{col} = 0 AND t.resolved = 0 "
            f"ORDER BY t.signal_score DESC, t.timestamp DESC").fetchall()

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

def get_pending_resolution_trades(min_age_hours=1, limit=300):
    """Trades not yet resolved, old enough for the market to have closed."""
    cutoff = int(time.time()) - (min_age_hours * 3600)
    with get_db() as db:
        return db.execute(
            "SELECT id, condition_id, side, outcome, price, size, usdc_value, wallet_address, "
            "title, slug, event_slug, signal_score, alerted_paid, alerted_free, timestamp "
            "FROM trades WHERE resolved = 0 AND timestamp < ? AND condition_id != '' "
            "ORDER BY timestamp ASC LIMIT ?",
            (cutoff, limit)).fetchall()


def was_recently_alerted(slug, hours=2, tier="paid"):
    """True if an alert for this market slug was sent within the past N hours."""
    if not slug:
        return False
    cutoff = int(time.time()) - (hours * 3600)
    with get_db() as db:
        row = db.execute(
            "SELECT 1 FROM alert_log WHERE tier = ? AND content LIKE ? AND sent_at > ?",
            (tier, f"%{slug}%", cutoff)).fetchone()
    return row is not None

def mark_trade_resolved(trade_id, won, pnl=0.0):
    with get_db() as db:
        db.execute(
            "UPDATE trades SET resolved = 1, won = ?, pnl = ? WHERE id = ?",
            (won, round(pnl, 2), trade_id))

# ── Resolution-based stats ───────────────────────────────────────────────────

def get_weekly_stats():
    cutoff = int(time.time()) - (7 * 86400)
    with get_db() as db:
        trades = db.execute("SELECT COUNT(*) FROM trades WHERE timestamp > ?", (cutoff,)).fetchone()[0]
        resolved = db.execute(
            "SELECT COUNT(*) FROM trades WHERE resolved = 1 AND timestamp > ?", (cutoff,)).fetchone()[0]
        wins = db.execute(
            "SELECT COUNT(*) FROM trades WHERE resolved = 1 AND won = 1 AND timestamp > ?", (cutoff,)).fetchone()[0]
        convergences = db.execute(
            "SELECT COUNT(*) FROM convergence_events WHERE detected_at > ?", (cutoff,)).fetchone()[0]
        alerts_paid = db.execute(
            "SELECT COUNT(*) FROM alert_log WHERE tier = 'paid' AND sent_at > ?", (cutoff,)).fetchone()[0]
    return {"trades_7d": trades, "resolved_7d": resolved, "wins_7d": wins,
            "convergences_7d": convergences, "alerts_paid_7d": alerts_paid}

def get_weekly_top_resolved_trades(limit=3):
    cutoff = int(time.time()) - (7 * 86400)
    with get_db() as db:
        return db.execute(
            "SELECT t.*, w.name FROM trades t JOIN wallets w ON t.wallet_address = w.address "
            "WHERE t.resolved = 1 AND t.timestamp > ? AND t.signal_score >= 50 "
            "ORDER BY t.pnl DESC LIMIT ?",
            (cutoff, limit)).fetchall()

def get_category_performance(days=7):
    cutoff = int(time.time()) - (days * 86400)
    with get_db() as db:
        rows = db.execute(
            "SELECT category, COUNT(*) as total, "
            "SUM(CASE WHEN won = 1 THEN 1 ELSE 0 END) as wins "
            "FROM trades WHERE resolved = 1 AND timestamp > ? AND category != '' "
            "GROUP BY category HAVING total >= 2 "
            "ORDER BY CAST(wins AS REAL) / total DESC",
            (cutoff,)).fetchall()
    return [dict(r) for r in rows]

def get_signal_accuracy(signal_min=60, days=30):
    cutoff = int(time.time()) - (days * 86400)
    with get_db() as db:
        total = db.execute(
            "SELECT COUNT(*) FROM trades WHERE resolved = 1 AND signal_score >= ? AND timestamp > ?",
            (signal_min, cutoff)).fetchone()[0]
        wins = db.execute(
            "SELECT COUNT(*) FROM trades WHERE resolved = 1 AND won = 1 AND signal_score >= ? AND timestamp > ?",
            (signal_min, cutoff)).fetchone()[0]
    return {"total": total, "wins": wins,
            "rate": round(wins / total * 100, 1) if total > 0 else 0.0}

def get_whale_of_week():
    """Best wallet by win rate with ≥3 resolved trades in the last 7 days."""
    cutoff = int(time.time()) - (7 * 86400)
    with get_db() as db:
        row = db.execute(
            "SELECT t.wallet_address, w.name, w.score, w.total_pnl, "
            "COUNT(*) as trade_count, "
            "SUM(CASE WHEN t.won = 1 THEN 1 ELSE 0 END) as wins, "
            "SUM(t.pnl) as week_pnl "
            "FROM trades t JOIN wallets w ON t.wallet_address = w.address "
            "WHERE t.resolved = 1 AND t.timestamp > ? "
            "GROUP BY t.wallet_address HAVING trade_count >= 3 "
            "ORDER BY CAST(wins AS REAL) / trade_count DESC, week_pnl DESC LIMIT 1",
            (cutoff,)).fetchone()
    return dict(row) if row else None

def get_whale_of_week_trades(address, days=7):
    cutoff = int(time.time()) - (days * 86400)
    with get_db() as db:
        return db.execute(
            "SELECT * FROM trades WHERE wallet_address = ? AND resolved = 1 AND timestamp > ? "
            "ORDER BY pnl DESC LIMIT 5",
            (address, cutoff)).fetchall()

MIN_SIGNAL_FREE_THRESHOLD = 50  # Must match MIN_SIGNAL_FREE in whale_tracker/main.py

def get_missed_paid_alerts(days=7):
    """Count Pro-only alerts (actually sent to paid, not sent to free due to score gate)."""
    cutoff = int(time.time()) - (days * 86400)
    with get_db() as db:
        # Only count trades that scored high enough for free tier but weren't sent yet
        # (excludes trades that were sub-threshold and permanently marked alerted_free=1)
        pro_only = db.execute(
            "SELECT COUNT(*) FROM trades WHERE alerted_paid = 1 AND alerted_free = 0 "
            "AND signal_score >= ? AND timestamp > ?",
            (MIN_SIGNAL_FREE_THRESHOLD, cutoff)).fetchone()[0]
        conv_pro_only = db.execute(
            "SELECT COUNT(*) FROM convergence_events WHERE alerted = 1 AND signal_score < 70 "
            "AND detected_at > ?", (cutoff,)).fetchone()[0]
    return {"pro_only_alerts": pro_only, "pro_only_convergences": conv_pro_only}

# ── Positions ────────────────────────────────────────────────────────────────

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

# ── Convergence ──────────────────────────────────────────────────────────────

def insert_convergence(event_data, dedup_hours=6):
    """Insert a convergence event; skip if one for this condition_id exists within dedup_hours."""
    cutoff = int(time.time()) - (dedup_hours * 3600)
    with get_db() as db:
        existing = db.execute(
            "SELECT id FROM convergence_events WHERE condition_id = ? AND detected_at > ?",
            (event_data["condition_id"], cutoff)).fetchone()
        if existing:
            return False  # Duplicate within window
        db.execute(
            "INSERT INTO convergence_events (condition_id, title, slug, wallet_count, wallets, "
            "wallet_names, dominant_side, total_size, signal_score, detected_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (event_data["condition_id"], event_data["title"], event_data["slug"],
             event_data["wallet_count"], json.dumps(event_data["wallets"]),
             json.dumps(event_data.get("wallet_names", [])),
             event_data["dominant_side"], event_data["total_size"],
             event_data["signal_score"], int(time.time())))
        return True

def get_unalerted_convergences():
    with get_db() as db:
        return db.execute(
            "SELECT * FROM convergence_events WHERE alerted = 0 ORDER BY signal_score DESC").fetchall()

def mark_convergence_alerted(cid):
    with get_db() as db:
        db.execute("UPDATE convergence_events SET alerted = 1 WHERE id = ?", (cid,))

# ── Watchlists ───────────────────────────────────────────────────────────────

def add_watchlist(telegram_id, keyword):
    keyword = keyword.lower().strip()[:50]
    with get_db() as db:
        try:
            db.execute(
                "INSERT OR IGNORE INTO watchlists (telegram_id, keyword, created_at) VALUES (?, ?, ?)",
                (telegram_id, keyword, int(time.time())))
            return db.execute("SELECT changes()").fetchone()[0] > 0
        except Exception:
            return False

def remove_watchlist(telegram_id, keyword):
    keyword = keyword.lower().strip()
    with get_db() as db:
        db.execute("DELETE FROM watchlists WHERE telegram_id = ? AND keyword = ?",
                   (telegram_id, keyword))

def get_user_watchlist(telegram_id):
    with get_db() as db:
        rows = db.execute(
            "SELECT keyword FROM watchlists WHERE telegram_id = ? ORDER BY created_at",
            (telegram_id,)).fetchall()
    return [r["keyword"] for r in rows]

def get_watchlist_matches(title):
    """Return telegram_ids whose watchlist keywords appear in title."""
    title_lower = title.lower()
    with get_db() as db:
        rows = db.execute("SELECT DISTINCT telegram_id, keyword FROM watchlists").fetchall()
    return list({r["telegram_id"] for r in rows if r["keyword"] in title_lower})

# ── Stats & Alerts ───────────────────────────────────────────────────────────

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

def get_alert_history(days=7):
    cutoff = int(time.time()) - (days * 86400)
    with get_db() as db:
        alerts = db.execute(
            "SELECT COUNT(*) FROM alert_log WHERE tier = 'paid' AND sent_at > ?",
            (cutoff,)).fetchone()[0]
        resolved = db.execute(
            "SELECT COUNT(*) FROM trades WHERE resolved = 1 AND alerted_paid = 1 AND timestamp > ?",
            (cutoff,)).fetchone()[0]
        wins = db.execute(
            "SELECT COUNT(*) FROM trades WHERE resolved = 1 AND won = 1 AND alerted_paid = 1 AND timestamp > ?",
            (cutoff,)).fetchone()[0]
        top = db.execute(
            "SELECT title, signal_score, won, pnl FROM trades "
            "WHERE resolved = 1 AND alerted_paid = 1 AND timestamp > ? AND signal_score >= 50 "
            "ORDER BY pnl DESC LIMIT 3",
            (cutoff,)).fetchall()
    return {
        "days": days, "alerts": alerts, "resolved": resolved, "wins": wins,
        "top_signals": [dict(t) for t in top],
    }


# ── Key-value store for persistent state ─────────────────────────────────────

def _ensure_kv_table(db):
    db.execute(
        "CREATE TABLE IF NOT EXISTS kv_store "
        "(key TEXT PRIMARY KEY, value TEXT, updated_at INTEGER DEFAULT 0)")


def kv_set(key, value_obj):
    with get_db() as db:
        _ensure_kv_table(db)
        db.execute(
            "INSERT OR REPLACE INTO kv_store (key, value, updated_at) VALUES (?, ?, ?)",
            (key, json.dumps(value_obj), int(time.time())))


def kv_get(key, default=None):
    with get_db() as db:
        _ensure_kv_table(db)
        row = db.execute("SELECT value FROM kv_store WHERE key = ?", (key,)).fetchone()
    if row:
        try:
            return json.loads(row[0])
        except Exception:
            return default
    return default
