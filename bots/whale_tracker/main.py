#!/usr/bin/env python3
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
    get_wallet_trade_count,
    get_unalerted_trades, mark_trade_alerted, get_recent_trades_for_condition,
    upsert_position, get_wallet_positions as db_get_positions,
    insert_convergence, get_unalerted_convergences, mark_convergence_alerted,
    get_stats, log_alert,
    get_pending_resolution_trades, mark_trade_resolved,
    get_weekly_stats, get_weekly_top_resolved_trades,
    get_category_performance, get_whale_of_week, get_whale_of_week_trades,
    get_watchlist_matches, was_recently_alerted)
from shared.polymarket_api import (
    get_wallet_activity, get_wallet_positions, get_gamma_markets,
    classify_market_category, extract_wallet_info, get_market_by_condition,
    get_market_activity, get_recent_global_trades)
from shared.notifier import (
    send_paid_alert, send_free_alert, send_admin_alert, send_dm,
    format_whale_trade, format_convergence_alert, format_convergence_teaser,
    format_daily_digest, format_weekly_digest, format_whale_of_week,
    format_category_leaderboard, format_resolution_post, format_score_breakdown,
    SUBSCRIBE_LINK)
from shared.ai_client import ask_ai, get_cost_summary
from shared.payments_db import get_subscriber_stats, get_subscriber

SEED_WALLETS = [
    # Original seed wallets — confirmed inactive as of 2026-03-13
    # Kept for DB compatibility; discover_whales() now finds live traders from market activity
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
MIN_SIGNAL_FREE = 50
MIN_TRADE_VALUE = 500
TRADE_CHECK_INTERVAL = 3
DISCOVERY_INTERVAL = 6
SCORING_INTERVAL = 4
DIGEST_TIME = "09:00"
WEEKLY_DIGEST_DAY = "monday"
WHALE_OF_WEEK_DAY = "sunday"
CATEGORY_BOARD_DAY = "sunday"
FREE_DELAY = 1800  # 30 minutes


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
    # Always check alerts — free-tier delay means old trades may be ready now
    _send_trade_alerts()
    if new_trades_total > 0:
        _check_convergence()


def _process_trade(raw_trade, wallet):
    addr = raw_trade.get("proxyWallet", "")
    tx_hash = raw_trade.get("transactionHash", "")
    if not tx_hash: return None
    trade_id = f"{addr[:10]}_{tx_hash}_{raw_trade.get('timestamp', 0)}"
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
        "timestamp": int(time.time()),  # detection time — free delay is measured from here
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
    # ── Paid tier: immediate ──────────────────────────────────────────────────
    paid_trades = get_unalerted_trades("paid")
    for t in paid_trades:
        if t["signal_score"] >= MIN_SIGNAL_PAID:
            slug = t.get("event_slug") or t.get("slug", "")
            if slug and was_recently_alerted(slug, hours=2, tier="paid"):
                log(f"  [DEDUP] {slug[:30]} already alerted within 2h, skipping")
            else:
                wi = {"name": t.get("name", ""), "score": t.get("score", 50),
                      "total_trades": t.get("w_total", 0), "wins": t.get("wins", 0),
                      "avg_trade_size": 0}
                w = get_wallet(t["wallet_address"])
                if w: wi["avg_trade_size"] = w["avg_trade_size"]
                msg = format_whale_trade(dict(t), wi, t["signal_score"])
                if t["signal_score"] >= 70:
                    msg += "\n\n" + format_score_breakdown(dict(t), wi, t["signal_score"])
                send_paid_alert(msg)
                time.sleep(0.5)
        mark_trade_alerted(t["id"], "paid")

    # ── Free tier: 30-min delay ───────────────────────────────────────────────
    free_trades = get_unalerted_trades("free")
    for t in free_trades:
        age = int(time.time()) - t["timestamp"]
        if age >= FREE_DELAY and t["signal_score"] < MIN_SIGNAL_FREE:
            log(f"  [FREE DROP] trade {t['id']} score={t['signal_score']} < {MIN_SIGNAL_FREE}, skipping free alert")
        if age >= FREE_DELAY and t["signal_score"] >= MIN_SIGNAL_FREE:
            slug = t.get("event_slug") or t.get("slug", "")
            if slug and was_recently_alerted(slug, hours=2, tier="free"):
                log(f"  [DEDUP-FREE] {slug[:30]} already alerted within 2h, skipping")
            else:
                wi = {"name": t.get("name", ""), "score": t.get("score", 50),
                      "total_trades": t.get("w_total", 0), "wins": t.get("wins", 0),
                      "avg_trade_size": 0}
                msg = format_whale_trade(dict(t), wi, t["signal_score"], free=True)
                send_free_alert(msg)
                time.sleep(0.5)
                # DM users who are watching this market keyword
                _notify_watchlist(t["title"], msg)
        if age >= FREE_DELAY:
            mark_trade_alerted(t["id"], "free")


def _notify_watchlist(title, alert_msg):
    """Send DM to Pro subscribers whose watchlist keywords match this trade's market title."""
    try:
        watchers = get_watchlist_matches(title)
        for telegram_id in watchers:
            sub = get_subscriber(telegram_id)
            if not sub or sub["status"] not in ("active", "trial"):
                continue
            send_dm(telegram_id, f"👀 <b>Watchlist match:</b>\n\n{alert_msg}")
            time.sleep(0.3)
    except Exception as e:
        log(f"  Watchlist notify error: {e}")


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
        sig = min(100, int(len(unique) * 15 + agree * 20 + min(30, total_size / 1000) + avg_sc * 0.2))
        if sig >= 40:
            insert_convergence({
                "condition_id": cond_id, "title": trades[0]["title"],
                "slug": trades[0].get("event_slug") or trades[0].get("slug", ""),
                "wallet_count": len(unique), "wallets": list(unique),
                "wallet_names": names, "dominant_side": dom,
                "total_size": total_size, "signal_score": sig})

    for c in get_unalerted_convergences():
        cd = dict(c)
        stored_names = json.loads(cd.get("wallet_names", "[]"))
        cd["wallet_names"] = stored_names if stored_names else json.loads(cd.get("wallets", "[]"))

        # Build per-wallet breakdown for the alert
        wallet_details = []
        raw_wallets = json.loads(c.get("wallets", "[]"))
        for addr in raw_wallets[:5]:
            w = get_wallet(addr)
            if w:
                # Find their trade for this condition
                trades_for_cond = [t for t in by_market.get(c["condition_id"], [])
                                   if t["wallet_address"] == addr]
                size = sum(t["usdc_value"] for t in trades_for_cond)
                wallet_details.append({
                    "name": w["name"] or addr[:8],
                    "score": w["score"],
                    "size": size,
                })

        msg = format_convergence_alert(cd, wallet_details=wallet_details or None)
        send_paid_alert(msg, alert_type="convergence")

        # Free always gets a teaser only — never the full alert
        # (high-score convergences are the product's most valuable signal)
        send_free_alert(format_convergence_teaser(cd), alert_type="convergence")

        mark_convergence_alerted(c["id"])
        time.sleep(0.5)


# ── Trade resolution ──────────────────────────────────────────────────────────

def resolve_trades():
    """Check Polymarket for resolved markets and update trade win/loss outcomes."""
    pending = get_pending_resolution_trades(min_age_hours=1)
    if not pending:
        return

    # De-duplicate by condition_id to minimize API calls
    checked_conditions = {}
    for t in pending:
        cid = t["condition_id"]
        if not cid or cid in checked_conditions:
            continue
        try:
            market = get_market_by_condition(cid)
            if not market:
                checked_conditions[cid] = None
                continue
            if not market.get("closed", False):
                checked_conditions[cid] = None
                continue
            prices = market.get("outcomePrices", [])
            if isinstance(prices, str):
                try: prices = json.loads(prices)
                except: prices = []
            if not prices:
                checked_conditions[cid] = None
                continue
            yes_price = float(prices[0])
            if yes_price >= 0.99:
                checked_conditions[cid] = "YES"
            elif yes_price <= 0.01:
                checked_conditions[cid] = "NO"
            else:
                checked_conditions[cid] = None  # Not yet resolved
        except Exception as e:
            log(f"  Resolution check error {cid[:12]}: {e}")
            checked_conditions[cid] = None

    resolved_count = 0
    for t in pending:
        cid = t["condition_id"]
        resolution = checked_conditions.get(cid)
        if not resolution:
            continue
        side = t["side"]
        outcome = t["outcome"]
        won = 0
        if side == "BUY":
            if outcome.upper() == "YES" and resolution == "YES": won = 1
            elif outcome.upper() == "NO" and resolution == "NO": won = 1
        elif side == "SELL":
            if outcome.upper() == "YES" and resolution == "NO": won = 1
            elif outcome.upper() == "NO" and resolution == "YES": won = 1
        # PnL: BUY YES won → earned (1 - entry) per share; lost → lost entry price
        price = t["price"]
        size = t["size"]
        pnl = size * (1.0 - price) if won else -size * price
        mark_trade_resolved(t["id"], won, round(pnl, 2))
        resolved_count += 1

        # Resolution post — only for trades that were actually alerted
        if t.get("alerted_paid") or t.get("alerted_free"):
            try:
                w = get_wallet(t["wallet_address"])
                wallet_name = (w["name"] if w else None) or t["wallet_address"][:10]
                msg = format_resolution_post(dict(t), wallet_name, won)
                send_paid_alert(msg, alert_type="resolution")
                send_free_alert(
                    msg + f"\n\n<i>Get real-time signals before markets resolve — <a href='{SUBSCRIBE_LINK}'>Subscribe →</a></i>",
                    alert_type="resolution")
                time.sleep(0.5)
            except Exception as e:
                log(f"  Resolution post error: {e}")

    if resolved_count:
        log(f"Resolved {resolved_count} trades")


# ── Scheduled digests ─────────────────────────────────────────────────────────

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
                          (int(time.time()) - 86400,)).fetchone()[0]

    try:
        sub_stats = get_subscriber_stats()
        sub_count = sub_stats.get("active", 0)
    except Exception:
        sub_count = 0

    msg = format_daily_digest([dict(w) for w in top], stats["trades_24h"], conv,
                               ai_summary, sub_count=sub_count)
    send_paid_alert(msg, alert_type="digest")
    costs = get_cost_summary()
    send_admin_alert(f"API costs today: ${costs['today_cost']:.4f} ({costs['today_calls']} calls)",
                     silent=True)


def send_weekly_digest():
    print(f"\n[{datetime.now(timezone.utc).strftime('%H:%M UTC')}] Weekly digest...")
    top = get_top_wallets(10)
    weekly_stats = get_weekly_stats()
    top_trades = [dict(t) for t in get_weekly_top_resolved_trades(limit=3)]
    ai_summary = ""
    try:
        wd = [{"name": w["name"] or w["address"][:10], "score": w["score"]} for w in top[:5]]
        ai_summary = ask_ai(
            f"Weekly prediction market summary (3-4 sentences). "
            f"Top whales: {json.dumps(wd)}. "
            f"Stats: {weekly_stats['trades_7d']} trades, "
            f"{weekly_stats['wins_7d']}/{weekly_stats['resolved_7d']} resolved correct. "
            f"What themes dominated this week?",
            system="Concise prediction market analyst.", use_cache=False, max_tokens=350)
    except Exception as e:
        ai_summary = f"AI unavailable: {e}"

    try:
        sub_stats = get_subscriber_stats()
        sub_count = sub_stats.get("active", 0)
    except Exception:
        sub_count = 0

    msg = format_weekly_digest([dict(w) for w in top], weekly_stats, top_trades,
                                ai_summary, sub_count=sub_count)
    send_paid_alert(msg, alert_type="weekly_digest")

    # Free channel gets summary without trade-level data
    free_msg = format_weekly_digest([dict(w) for w in top[:3]], weekly_stats, [],
                                     ai_summary="", sub_count=sub_count)
    free_msg += f"\n\n<i>Get the full weekly breakdown + top trades — <a href='{SUBSCRIBE_LINK}'>Subscribe →</a></i>"
    send_free_alert(free_msg, alert_type="weekly_digest")


def send_whale_of_week():
    print(f"\n[{datetime.now(timezone.utc).strftime('%H:%M UTC')}] Whale of the week...")
    whale = get_whale_of_week()
    if not whale:
        log("No qualifying whale this week (need ≥3 resolved trades)")
        return
    trades = [dict(t) for t in get_whale_of_week_trades(whale["wallet_address"])]
    msg = format_whale_of_week(whale, trades)
    send_paid_alert(msg, alert_type="whale_of_week")


def send_category_leaderboard():
    print(f"\n[{datetime.now(timezone.utc).strftime('%H:%M UTC')}] Category leaderboard...")
    stats = get_category_performance(days=7)
    if not stats:
        log("No resolved trades for category leaderboard")
        return
    msg = format_category_leaderboard(stats, days=7)
    send_paid_alert(msg, alert_type="category_leaderboard")
    # Free gets a teaser — signal accuracy by category is Pro intelligence
    free_teaser = (
        f"📊 <b>Weekly Category Performance</b>\n\n"
        f"Which market categories are whale signals calling correctly this week?\n"
        f"Crypto, Politics, Sports, Finance — full breakdown is live for Pro members.\n\n"
        f"<i><a href='{SUBSCRIBE_LINK}'>Subscribe to see it →</a></i>")
    send_free_alert(free_teaser, alert_type="category_leaderboard")


# ── Wallet management ─────────────────────────────────────────────────────────

DISCOVERY_MIN_USDC = 300   # Minimum total USDC volume in recent global trades to qualify


def discover_whales():
    print(f"\n[{datetime.now(timezone.utc).strftime('%H:%M UTC')}] Discovering whales...")
    from shared.database import get_db
    with get_db() as db:
        existing = set(r[0] for r in db.execute("SELECT address FROM wallets").fetchall())

    # Aggregate recent global trades by wallet to find active high-volume traders
    try:
        recent_trades = get_recent_global_trades(limit=500)
    except Exception as e:
        log(f"  Discovery fetch error: {e}")
        return

    wallet_volume = defaultdict(lambda: {"usdc": 0.0, "name": "", "pseudonym": "",
                                          "profile_image": ""})
    for t in recent_trades:
        addr = t.get("proxyWallet", "")
        if not addr:
            continue
        size = float(t.get("size", 0) or 0)
        price = float(t.get("price", 0) or 0)
        wallet_volume[addr]["usdc"] += size * price
        if t.get("name"):
            wallet_volume[addr]["name"] = t["name"]
        if t.get("pseudonym"):
            wallet_volume[addr]["pseudonym"] = t["pseudonym"]
        if t.get("profileImage"):
            wallet_volume[addr]["profile_image"] = t["profileImage"]

    found = 0
    for addr, data in wallet_volume.items():
        if data["usdc"] < DISCOVERY_MIN_USDC:
            continue
        if addr in existing:
            # Update last_active for known whales that are still trading
            upsert_wallet(addr, last_active=int(time.time()))
            continue
        try:
            upsert_wallet(addr,
                          name=data["name"],
                          pseudonym=data["pseudonym"],
                          tier=2, score=45.0,
                          profile_image=data["profile_image"],
                          last_active=int(time.time()))
            existing.add(addr)
            found += 1
            log(f"  Discovered: {data['name'] or addr[:12]} (${data['usdc']:.0f})")
        except Exception as e:
            log(f"  Error adding {addr[:12]}: {e}")

    if found:
        log(f"Discovered {found} new wallets from recent trades")
    else:
        log(f"No new wallets found (checked {len(wallet_volume)} active traders)")


def update_scores():
    print(f"\n[{datetime.now(timezone.utc).strftime('%H:%M UTC')}] Updating scores...")
    wallets = get_active_wallets(min_score=0, limit=200)
    updated = 0
    for w in wallets:
        try:
            positions = get_wallet_positions(w["address"], limit=50)
            total_pnl = sum(float(p.get("cashPnl", 0) or 0) for p in positions)
            total_initial = sum(float(p.get("initialValue", 0) or 0) for p in positions)
            wins = sum(1 for p in positions if float(p.get("cashPnl", 0) or 0) > 0)
            losses = sum(1 for p in positions if float(p.get("cashPnl", 0) or 0) < 0)
            recent = get_wallet_trades(w["address"], limit=50)
            trade_count = get_wallet_trade_count(w["address"])
            avg_size = sum(t["usdc_value"] for t in recent) / len(recent) if recent else 0
            score = 60.0 if w.get("tier", 2) == 1 else 45.0
            if total_initial > 0:
                roi = total_pnl / total_initial
                score = score - 17.5 + min(35, max(0, 17.5 + roi * 35))
            total_resolved = wins + losses
            if total_resolved > 5:
                score = score - 15 + min(30, max(0, (wins / total_resolved) * 40))
            days_inactive = (time.time() - (w["last_active"] or w["discovered_at"])) / 86400
            if days_inactive < 1: score += 10
            elif days_inactive < 7: score += 5
            elif days_inactive > 30: score -= 10
            if trade_count > 20: score += 5
            elif trade_count > 5: score += 3
            score = min(100, max(0, score))
            cats = defaultdict(lambda: {"w": 0, "t": 0})
            for p in positions:
                c = classify_market_category(p.get("title", ""))
                cats[c]["t"] += 1
                if float(p.get("percentPnl", 0) or 0) > 0: cats[c]["w"] += 1
            best_cat, best_wr = "", 0
            for c, d in cats.items():
                if d["t"] >= 3:
                    wr = d["w"] / d["t"]
                    if wr > best_wr: best_cat, best_wr = c, wr
            update_wallet_score(w["address"], round(score, 1),
                total_trades=max(w["total_trades"], trade_count), wins=wins, losses=losses,
                total_pnl=round(total_pnl, 2), avg_trade_size=round(avg_size, 2),
                best_category=best_cat, best_category_winrate=round(best_wr * 100, 1))
            updated += 1
        except Exception as e:
            log(f"  Error scoring {w['address'][:10]}: {e}")
    log(f"Updated {updated} wallets")
    resolve_trades()


def seed_wallets():
    for s in SEED_WALLETS:
        upsert_wallet(s["address"], name=s["name"], tier=s["tier"],
                      score=60.0 if s["tier"] == 1 else 45.0)
    log(f"Seeded {len(SEED_WALLETS)} wallets")


def startup():
    print("=" * 50)
    print("  WhalePulse Whale Tracker")
    print("=" * 50)
    init_db()
    seed_wallets()
    log("Discovering active wallets from market activity...")
    discover_whales()
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
    schedule.every().monday.at("09:00").do(send_weekly_digest)
    schedule.every().sunday.at("10:00").do(send_whale_of_week)
    schedule.every().sunday.at("18:00").do(send_category_leaderboard)
    print(f"\nTrade checks: every {TRADE_CHECK_INTERVAL}min | Discovery: every {DISCOVERY_INTERVAL}h")
    print(f"Scores: every {SCORING_INTERVAL}h | Daily digest: {DIGEST_TIME} UTC")
    print("Weekly digest: Mon 09:00 | Whale of Week: Sun 10:00 | Category board: Sun 18:00")
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
