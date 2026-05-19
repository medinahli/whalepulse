"""
WhalePulse Payments Database
Manages subscriber records, links Stripe customers to Telegram users.
"""
import sqlite3
import time
from pathlib import Path
from contextlib import contextmanager

DB_PATH = Path(__file__).parent.parent / "data" / "whalepulse.db"

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
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = _dict_row_factory
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
            in_channel INTEGER DEFAULT 0,
            used_trial INTEGER DEFAULT 0,
            referred_by INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id INTEGER NOT NULL,
            referred_id INTEGER NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at INTEGER DEFAULT 0,
            rewarded_at INTEGER DEFAULT 0,
            UNIQUE(referred_id)
        );
        CREATE INDEX IF NOT EXISTS idx_sub_stripe ON subscribers(stripe_customer_id);
        CREATE INDEX IF NOT EXISTS idx_sub_status ON subscribers(status);
        CREATE INDEX IF NOT EXISTS idx_sub_expires ON subscribers(expires_at);
        CREATE INDEX IF NOT EXISTS idx_referrals_referrer ON referrals(referrer_id);
        """)
        # Migrate existing tables (safe — ignored if columns already exist)
        for col, definition in [
            ("used_trial", "INTEGER DEFAULT 0"),
            ("referred_by", "INTEGER DEFAULT 0"),
        ]:
            try:
                db.execute(f"ALTER TABLE subscribers ADD COLUMN {col} {definition}")
            except Exception:
                pass

_SUBSCRIBER_COLUMNS = frozenset({
    "telegram_username", "stripe_customer_id", "stripe_subscription_id",
    "plan", "status", "started_at", "expires_at", "cancelled_at",
    "last_payment", "total_paid", "in_channel", "used_trial", "referred_by",
})


def upsert_subscriber(telegram_id, **kwargs):
    bad = set(kwargs) - _SUBSCRIBER_COLUMNS
    if bad:
        raise ValueError(f"upsert_subscriber: unknown column(s): {bad}")
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
            "SELECT * FROM subscribers WHERE status IN ('active', 'trial') ORDER BY started_at DESC"
        ).fetchall()

def get_expired_subscribers():
    now = int(time.time())
    with get_db() as db:
        return db.execute(
            "SELECT * FROM subscribers WHERE status = 'active' AND expires_at > 0 AND expires_at < ?",
            (now,)).fetchall()

def get_trial_subscribers():
    with get_db() as db:
        return db.execute(
            "SELECT * FROM subscribers WHERE status = 'trial' ORDER BY expires_at ASC"
        ).fetchall()

def get_expired_trials():
    now = int(time.time())
    with get_db() as db:
        return db.execute(
            "SELECT * FROM subscribers WHERE status = 'trial' AND expires_at > 0 AND expires_at < ?",
            (now,)).fetchall()

def get_subscriber_stats():
    with get_db() as db:
        active = db.execute(
            "SELECT COUNT(*) FROM subscribers WHERE status = 'active'").fetchone()[0]
        trials = db.execute(
            "SELECT COUNT(*) FROM subscribers WHERE status = 'trial'").fetchone()[0]
        total = db.execute("SELECT COUNT(*) FROM subscribers").fetchone()[0]
        revenue = db.execute("SELECT SUM(total_paid) FROM subscribers").fetchone()[0] or 0
        return {"active": active, "trials": trials, "total": total, "total_revenue": revenue}

# ── Referrals ─────────────────────────────────────────────────────────────────

def claim_trial(telegram_id, telegram_username=""):
    """Atomically claim the trial slot. Returns True if this call wins the race, False if already claimed."""
    with get_db() as db:
        # Try to mark used_trial=1 on an existing row where it's not yet set
        db.execute(
            "UPDATE subscribers SET used_trial = 1 "
            "WHERE telegram_id = ? AND (used_trial = 0 OR used_trial IS NULL)",
            (telegram_id,))
        if db.execute("SELECT changes()").fetchone()[0] > 0:
            return True
        # Check if the user exists at all
        existing = db.execute(
            "SELECT telegram_id FROM subscribers WHERE telegram_id = ?", (telegram_id,)).fetchone()
        if not existing:
            # New user — insert with used_trial=1
            db.execute(
                "INSERT OR IGNORE INTO subscribers (telegram_id, telegram_username, used_trial) "
                "VALUES (?, ?, 1)",
                (telegram_id, telegram_username))
            return db.execute("SELECT changes()").fetchone()[0] > 0
        return False  # User exists and already used their trial


def add_referral(referrer_id, referred_id):
    with get_db() as db:
        try:
            db.execute(
                "INSERT OR IGNORE INTO referrals (referrer_id, referred_id, status, created_at) "
                "VALUES (?, ?, 'pending', ?)",
                (referrer_id, referred_id, int(time.time())))
        except Exception:
            pass

def get_referral_count(telegram_id):
    with get_db() as db:
        return db.execute(
            "SELECT COUNT(*) FROM referrals WHERE referrer_id = ?", (telegram_id,)).fetchone()[0]

def get_completed_referral_count(telegram_id):
    with get_db() as db:
        return db.execute(
            "SELECT COUNT(*) FROM referrals WHERE referrer_id = ? AND status = 'completed'",
            (telegram_id,)).fetchone()[0]

def complete_referral(referred_id):
    """Mark referral as completed; return referrer_id or None."""
    with get_db() as db:
        db.execute(
            "UPDATE referrals SET status = 'completed' WHERE referred_id = ?", (referred_id,))
        row = db.execute(
            "SELECT referrer_id FROM referrals WHERE referred_id = ?", (referred_id,)).fetchone()
    return row["referrer_id"] if row else None
