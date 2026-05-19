# WhalePulse Engineer Log

---

## Session: 2026-03-13

### Session start ~18:30 UTC
Fresh start — no prior engineer log existed. Read issues.md (original QA report) and ideas.md in full. Performed full codebase audit.

---

### Fixes Applied

#### C1 / P0 — subscribe_bot.py deployed as service ✅
- Created `systemd/whalepulse-subscribe.service` with `User=botrunner`
- Linked to `/etc/systemd/system/` via `systemctl link`
- Enabled and started; confirmed active at 18:37 UTC

#### C2 / P0 — subscription_checker.py deployed as service ✅
- Created `systemd/whalepulse-subcheck.service` with `User=botrunner`
- Enabled and started; confirmed active at 18:37 UTC

#### P0-1 — Stripe webhook server was stopped ✅
- Caused by running `sudo systemctl stop whalepulse-payments` during H1 investigation
- Restarted immediately; service back up at 18:52 UTC
- NOTE: H1 (User=root) still present in `/etc/systemd/system/whalepulse-payments.service`
  but cannot be fixed via `systemctl link` because the file is a regular root-owned file.
  Requires manual edit of `/etc/systemd/system/whalepulse-payments.service` to change
  `User=root` to `User=botrunner` + add `AmbientCapabilities=CAP_NET_BIND_SERVICE`.

#### P0-3 / H3 — Admin and subscribe bots shared token (polling conflict) ✅
- Merged all admin commands (`/status`, `/whales`, `/trades`, `/costs`) into `subscribe_bot.py`
  with `_is_admin()` check (compares user.id to ADMIN_CHAT_ID)
- Stopped and disabled `whalepulse-admin.service`; only one polling bot now
- Conflict errors in subscribe-stderr.log stopped after ~5 min (old session expired)

#### C3 / P0-4 — All seed wallets inactive; zero alerts ✅
- Root cause: hardcoded SEED_WALLETS all stopped trading May 2025
- Old `discover_whales()` was a no-op (only added seeds if not in DB; seeds already in DB)
- **Fix:** Rewrote `discover_whales()` to fetch 500 recent global trades from
  `data-api.polymarket.com/trades`, aggregate by proxyWallet, and upsert any wallet
  with >= $300 total USDC volume as a new tracked wallet
- Added `get_recent_global_trades()` and `get_market_activity()` to `polymarket_api.py`
- `discover_whales()` now runs at startup + every 6h
- **Result at ~19:00 UTC:** 36 active wallets, 53 trades today, real alerts flowing to
  both Pro and Free channels. Confirmed trade + convergence alerts in alert_log.

#### H3 — Admin bot access control ✅
- `_is_admin()` helper + guard in all 4 handlers; confirmed in QA re-audit

#### H4 / P3-5 — No .gitignore ✅
- Created `.gitignore` excluding `config/.env`, `data/`, `logs/`, `venv/`, `*.db`

#### M1 — Duplicate DOMAIN in .env ✅
- Removed second `DOMAIN=https://api.whalepulsebot.com ` (with trailing space)

#### L2 / P2-2 — WEBHOOK_PORT missing from .env ✅
- Added `WEBHOOK_PORT=8080` to `config/.env`

#### L4 — Outdated AI model ID ✅
- Updated `shared/ai_client.py` from `claude-sonnet-4-20250514` to `claude-sonnet-4-6`

#### L3 / P2-1 — Free-tier dropped trades not logged ✅
- Added log line when a trade is dropped due to signal score < MIN_SIGNAL_FREE

#### L1 / P3-2 — Trade ID uses truncated tx_hash ✅
- Changed `tx_hash[:16]` to full `tx_hash` in `_process_trade()`

#### M5 — SQL from unvalidated column names ✅
- Added `_WALLET_COLUMNS` whitelist to `shared/database.py` `upsert_wallet()`
- Added `_SUBSCRIBER_COLUMNS` whitelist to `shared/payments_db.py` `upsert_subscriber()`
- Both raise `ValueError` if unknown column is passed

#### M3 / P3-1 — Convergence alerts show hex addresses ✅
- Added `wallet_names TEXT DEFAULT '[]'` column to `convergence_events` schema
- Added migration in `init_db()`
- Updated `insert_convergence()` to store wallet_names as JSON
- Updated `_check_convergence()` to use stored names (fallback to addresses if empty)

#### M4 — Scanner state lost on restart ✅
- Added `kv_store` table to `shared/database.py` with `kv_set()` / `kv_get()`
- Scanner persists `_previous_prices` and `_alerted_markets` to DB on every scan
- Loads state on startup
- Fixed bug: now saves state BEFORE clearing `_alerted_markets` (prevents empty-set persist)

#### P1-1 — Trial subscribers never removed on expiry ✅
- Added `get_expired_trials()` call in `subscription_checker.py` `check_expired()`
- Expired trials now get kicked from Pro channel and notified just like paid expiries

#### P1-2 — `complete_referral()` never called ✅
- Added `complete_referral(telegram_id)` call in `webhook_server.py`
  `handle_checkout_completed()` when metadata contains `referral_code`
- Referrer gets a Telegram notification when their referral subscribes

#### P1-3 — Convergence event deduplication ✅
- `insert_convergence()` now checks for an existing event for the same `condition_id`
  within the last 6 hours before inserting
- Returns `False` if duplicate; prevents alert spam across polling cycles

#### P2-6 — Scanner clear persisted as empty ✅
- Moved `_save_state()` call to BEFORE the `_alerted_markets.clear()` check
- The saved state always reflects the alerted markets before any in-memory clear

#### sqlite3.Row has no .get() — pre-existing bug (never hit before) ✅
- `sqlite3.Row` in Python 3.12 doesn't support `.get(key, default)` like a dict
- Was latent because DB had 0 trades; first triggered when alerts started flowing
- Fixed by replacing `conn.row_factory = sqlite3.Row` with a custom `_Row(dict)` subclass
  in both `shared/database.py` and `shared/payments_db.py`
- `_Row` supports both dict-style `.get()` and integer `[0]` index access

---

### Current Service Status (~19:05 UTC)

| Service | Status | Notes |
|---------|--------|-------|
| whalepulse-tracker | active | 36 wallets, 53 trades, alerts flowing |
| whalepulse-scanner | active | Price alerts working |
| whalepulse-payments | active (root) | H1/P1-4 still unresolved — needs manual service file edit |
| whalepulse-subscribe | active | All bot commands, incl. merged admin commands |
| whalepulse-subcheck | active | Handles both paid and trial expiry |
| whalepulse-admin | DISABLED | Merged into subscribe bot to fix P0-3 |

---

---

## Session: 2026-03-13 (continued ~23:30 UTC)

### Cross-check performed
Full audit of code vs. issues.md and ideas.md. Found:
- ideas.md was not updated to reflect the prior session's work — all P0/P1 completions were still ⬜
- Several features were implemented in code but undocumented (1.2, 1.3, 1.4, 1.5, 1.8, 2.2, 2.5, 2.6, 2.7)
- P2-3 (watchlist Pro gate) was unresolved in code despite no prior engineer comment
- notifier.py was improved by strategy expert (clearer alert language, BUY YES/NO framing, SUBSCRIBE_LINK constant)

### Fixes Applied

#### P2-3 — Watchlist DMs bypass Pro tier gate ✅
- Added `get_subscriber()` call in `_notify_watchlist()` in `whale_tracker/main.py`
- Free users with `/watch` keywords no longer receive watchlist DMs — Pro and trial only
- Import added: `get_subscriber` from `shared.payments_db`

#### 1.7 — Alert deduplication: same market within 2h ✅
- Added `was_recently_alerted(slug, hours, tier)` to `shared/database.py`
- Queries `alert_log` for recent paid alerts matching the market slug
- Paid tier loop in `_send_trade_alerts()` now skips duplicate market alerts within 2h
- Logs `[DEDUP]` when suppressed; still marks trade as `alerted_paid=1` to prevent re-evaluation
- Convergence alerts are unaffected (they always send)

#### 2.8 — Post to both channels when alerted market resolves ✅
- Added `format_resolution_post()` to `shared/notifier.py`
- `get_pending_resolution_trades()` query now includes title, slug, event_slug, signal_score, alerted_paid, alerted_free, timestamp
- After `mark_trade_resolved()`, `resolve_trades()` checks if trade was alerted (`alerted_paid=1` or `alerted_free=1`)
- If so, posts resolution to both Pro and Free channels with outcome, wallet name, original signal score
- Free channel post includes upgrade CTA

#### P2-1 — `/account` undercounting partially fixed ✅
- `get_missed_paid_alerts()` in `database.py` now filters by `signal_score >= 60`
- Unconditional `mark_trade_alerted` behavior retained (prevents per-cycle re-evaluation noise)
- `/account` "what you missed" count now reflects trades actually blocked by Pro gate, not sub-threshold drops

#### P2-2 — Paid tier mark-all bug (same pattern as P2-1) ✅
- Same fix applies: `was_recently_alerted()` dedup check now prevents duplicate paid sends
- Unconditional `mark_trade_alerted(paid)` retained for same reason

#### P2-6 — `update_wallet_score()` missing column whitelist ✅
- Added `_WALLET_COLUMNS` whitelist check at top of `update_wallet_score()`
- Raises `ValueError` on unknown column, consistent with `upsert_wallet()` and `upsert_subscriber()`

#### P3-1 — CLAUDE.md outdated ✅
- Updated service list: added whalepulse-subscribe, whalepulse-subcheck; noted whalepulse-admin disabled
- Updated bots table with correct port (80), correct service files, correct schedules
- Noted admin commands merged into subscribe_bot.py

#### Strategy change — MIN_SIGNAL_FREE lowered 60 → 40 ✅
- `MIN_SIGNAL_FREE` in `whale_tracker/main.py` changed from 60 to 40 (strategy decision)
- `MIN_SIGNAL_FREE_THRESHOLD` in `shared/database.py` updated to match (used in `get_missed_paid_alerts()`)
- `CLAUDE.md` Alert Tiers section updated to reflect new threshold
- Effect: more trades will now reach the free channel (score 40–59 now eligible, delayed 30 min)

#### docs/ideas.md — Backlog sync ✅
- Marked all completed items as ✅: P0.1, P0.2, P0.3, 1.1–1.5, 1.7–1.8, 2.2, 2.5–2.8
- Updated quick wins table to reflect actual state

---

### Known Remaining Issues

| ID | Notes |
|----|-------|
| P1-4 / H1 | Payments service still runs as root — requires manual `/etc/systemd/system/whalepulse-payments.service` edit (no passwordless sudo for cp/tee) |
| H2 / P2-8 | No TLS / no IP restriction on webhook — but RESOLVED via Cloudflare + Stripe sig verification (see issues.md) |
| P2-4 | Trial race condition — low severity, hard to fix without transactions |
| P2-5 | Wallet scores all level to 60 after update_scores() for new wallets |
| P2-7 | update_scores() uses unrealized PnL, not resolved win rate |
| P3-3 | AI rate limiter / cost logger not thread-safe (no current impact) |
| P3-4 | bot-farm and whalepulse share Polymarket API IP rate limit |
| P3-6 | total_trades caps at 50 in wallet table |
| quick win | Filter alerts for already-resolved markets (don't alert on closed market) |
| quick win | Welcome to Pro DM on day 1 explaining alert types |

