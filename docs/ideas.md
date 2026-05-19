# WhalePulse — Product Ideas & Improvement Suggestions

_Last updated: 2026-03-13_

**Status legend:** ✅ Done · 🔧 In Progress · ⬜ Todo

---

## P0 — Must fix before anything else (product is broken without these)

These are not "features." They are the reason paying subscribers are currently receiving zero value.

---

### P0.1 Replace inactive seed wallets and fix whale discovery ✅

**Problem:** All 10 hardcoded seed wallets last traded on Polymarket in May 2025, ~10 months ago. The tracker runs every 3 minutes and returns `Active: 0 | New trades: 0`. No alerts have been sent since launch. Paying subscribers receive nothing.

**Why this is P0:** A $29/mo product that sends zero alerts will churn every subscriber on their first billing cycle.

**Fix:**

1. **Immediate (today):** Manually replace the `SEED_WALLETS` list in `bots/whale_tracker/main.py` with currently-active high-volume addresses. Find them by:
   - Checking Polymarket leaderboard at `https://polymarket.com/leaderboard` for top traders by volume
   - Querying `GET /data-api/v2/activity?limit=100&orderBy=CASH_TRADED_DESC` for recent large trades
   - Pulling `get_gamma_markets()` in `polymarket_api.py`, then checking top-position holders per market

2. **Permanent (this week):** Rewrite `discover_whales()` in `whale_tracker/main.py` to actually discover new wallets:
   ```python
   # Pseudocode for real discovery:
   markets = await get_gamma_markets(limit=20, sort_by="volume_24h")
   for market in markets:
       activity = await get_wallet_activity(market["condition_id"], limit=50)
       for trade in activity:
           if trade["usdcValue"] >= MIN_TRADE_SIZE:
               upsert_wallet(address=trade["maker"], tier=2, source="discovery")
   ```
   Add `MIN_DISCOVERY_TRADE_SIZE = 2000` (USDC) as a config threshold.

3. **Discovery logging:** Post to Pro channel when a new Tier 2 wallet is added (see idea 3.4 below). Communicates that the system is actively improving.

**Acceptance criteria:** Tracker logs show `Active: N | New trades: M` (N > 0, M > 0) within one polling cycle.

---

### P0.2 Deploy subscribe_bot.py and subscription_checker.py as services ✅

**Problem (C1):** `subscribe_bot.py` has no systemd unit. Users cannot run `/subscribe`, `/account`, or `/cancel`. New subscriptions cannot be created. Revenue is broken.

**Problem (C2):** `subscription_checker.py` has no systemd unit. Expired subscribers are never removed from Pro channel. They receive paid content indefinitely for free.

**Fix — create two systemd unit files:**

`/etc/systemd/system/whalepulse-subscribe.service`:
```ini
[Unit]
Description=WhalePulse Subscribe Bot
After=network.target

[Service]
Type=simple
User=botrunner
WorkingDirectory=/home/botrunner/whalepulse
EnvironmentFile=/home/botrunner/whalepulse/config/.env
ExecStart=/home/botrunner/whalepulse/venv/bin/python bots/payments/subscribe_bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/whalepulse-subcheck.service`:
```ini
[Unit]
Description=WhalePulse Subscription Checker
After=network.target

[Service]
Type=simple
User=botrunner
WorkingDirectory=/home/botrunner/whalepulse
EnvironmentFile=/home/botrunner/whalepulse/config/.env
ExecStart=/home/botrunner/whalepulse/venv/bin/python bots/payments/subscription_checker.py
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

Then: `sudo systemctl daemon-reload && sudo systemctl enable --now whalepulse-subscribe whalepulse-subcheck`

**Acceptance criteria:** `/subscribe` and `/account` commands respond in Telegram. Expired users are kicked from Pro channel on next checker cycle.

---

### P0.3 Fix convergence alerts displaying raw hex addresses instead of wallet names ✅

**Problem (M3):** `insert_convergence()` in `database.py` stores the raw `wallets` hex-address JSON but discards the `wallet_names` list. When alerts are sent, they display `0x6af75d4...` instead of `gopfan, SilverBera`. This makes convergence alerts (the product's highest-signal feature) look like a raw data dump rather than an insight.

**Fix:** Add `wallet_names TEXT` column to `convergence_events` table and store the names alongside addresses:

```python
# database.py — add to convergence_events CREATE TABLE:
wallet_names TEXT,

# insert_convergence — add parameter and storage:
def insert_convergence(self, wallets, wallet_names, signal_score, ...):
    conn.execute("""
        INSERT INTO convergence_events (wallets, wallet_names, signal_score, ...)
        VALUES (?, ?, ?, ...)
    """, (json.dumps(wallets), json.dumps(wallet_names), signal_score, ...))
```

No schema migration needed if `init_db()` uses `CREATE TABLE IF NOT EXISTS` — the column will be added on next restart.

---

## P1 — High impact, achievable in a day or two each

### 1.1 Free-to-paid conversion nudges in alert messages ✅

The free channel already delays and filters alerts. Make the upsell explicit and specific inside each message.

**Current:** Free alerts have a generic "upgrade to Pro" one-liner appended.

**Change:** Replace generic text with a specific, time-aware nudge:
```
⚡ Pro members saw this 30 min earlier. Subscribe: /subscribe
```
For convergence alerts that scored 40–59 (visible only to Pro), send a teaser to free:
```
🐋 A convergence signal just fired — 3 whales, same direction.
Score: 47/100. Pro only. → /subscribe
```
This turns the absence of an alert into a visible, motivating gap.

---

### 1.2 Resolve trades automatically and track signal accuracy ✅

The `trades` table has `resolved`, `won`, and `pnl` fields that are never set. This means win rate calculations are based on zero data, and there is no accuracy story to tell subscribers.

**Why this is P1, not P2:** Provable accuracy is the single strongest conversion and retention argument. "Our signals called 7 of 10 markets correctly last month" beats any feature list.

**Implementation:**

1. Every 4h (hook into existing `update_scores()` scheduler):
   - Query `trades` where `resolved = 0` and `timestamp < now - 1h`
   - For each, call Polymarket's market endpoint using the trade's `condition_id` to check resolution status
   - If resolved: set `resolved = 1`, `won = 1/0` based on whether the whale's side (YES/NO) matched the outcome, compute `pnl`

2. Add to daily digest: "Signals with score ≥60: X won of Y resolved markets this week (Z%)"

3. Add to `/whales` admin command: per-wallet win rate becomes a real number

**Technical note:** Use `condition_id` as the lookup key on Polymarket — confirmed as the correct identifier in `polymarket_api.py`.

---

### 1.3 `/account` command — show what the free user specifically missed ✅

Right now `/account` just shows subscription status. When a free user runs it, show a concrete summary of what they missed:

```
📊 Your Free Account

Last 7 days:
• 12 Pro-only alerts fired (score 30–59)
• 3 convergence signals you didn't see
• Average Pro alert lead time: 34 min ahead of you

Upgrade to Pro → /subscribe ($29/mo)
```

**Implementation:** Query `alert_log` filtered by `tier='paid'` and `sent_at > now - 7 days`. Count rows. The `avg_lead_time` stat requires comparing `sent_at` for paid vs. free alerts on the same `trade_id` — already joinable.

This is a pure query + formatting change. No schema changes needed.

---

### 1.4 Trial period — 3 days free Pro access ✅

No trial exists. Add a `/trial` command (one-time use per `telegram_id`):

1. Check `used_trial` flag in `subscribers` table (column already exists in `payments_db.py`)
2. If eligible: send a 72h Telegram invite link to Pro channel using `create_chat_invite_link(expire_date=now+72h, member_limit=1)`
3. Insert row in `subscribers` with `status='trial'`, `expires_at = now + 72h`
4. Set `used_trial = 1`
5. When `subscription_checker.py` finds trial rows with `expires_at < now`: kick user, send Stripe checkout link: "Your trial ended. Keep access for $29/mo."

**Conversion note:** Trials on tools with clear, immediate value typically convert at 20–40%. The key is the "kick" moment — it must happen on time (which requires P0.2 to be done first).

**Add to `/start` message for new users:**
```
✨ New here? Try Pro free for 3 days → /trial
```

---

### 1.5 Weekly performance digest ✅

The daily digest summarizes the previous 24h. A weekly digest on Monday morning (08:00 UTC) is far more shareable and retentive.

**Content:**
- Top 3 whale calls from the past week with outcomes (requires trade resolution from 1.2)
- Best-performing wallet (by win rate, ≥3 trades in 7 days)
- Most-alerted market category
- Stat: "Our signals were right X% of the time this week"
- One AI sentence: biggest market theme of the week

**Why:** Weekly recaps are social — subscribers screenshot and share them, driving organic acquisition. They also keep inactive free users engaged enough to not leave the free channel.

**Send to:** Both channels. Free gets the summary; Pro gets the full breakdown with trade-level data.

**Scheduler:** Add to the existing `schedule` block in `whale_tracker/main.py`:
```python
schedule.every().monday.at("08:00").do(run_weekly_digest)
```

---

### 1.6 Show market resolution date and time-to-close in alerts ⬜

**Problem:** An alert for a market resolving in 4 hours is far more actionable than one resolving in 6 months. Currently, alerts show no closing date context.

**Fix:** In `notifier.py:format_whale_trade()`, add:
```
⏱ Market closes: Mar 15 (2 days)
```
The `end_date` field is available in Polymarket's market API response. Cache it alongside the market title in the existing trade record.

**Why it justifies the price:** Time-sensitive alpha is the core value prop. Showing when a market closes makes every alert feel more urgent and actionable.

---

### 1.7 Alert de-duplication: suppress re-alerts on same market within 2h ✅ (applied to both tiers)

**Problem:** If multiple whales buy YES on the same market within 30 minutes, subscribers receive 3–5 nearly identical alerts. This trains them to ignore alerts (alert fatigue) and makes the product feel noisy.

**Fix:** Add a `market_last_alerted` field to the `alert_log` or check `alert_log` before sending:
```python
recent = conn.execute("""
    SELECT 1 FROM alert_log
    WHERE content LIKE ? AND sent_at > ?
""", (f"%{market_slug}%", datetime.now() - timedelta(hours=2))).fetchone()
```
If a recent alert exists for the same market: skip individual trade alerts, but record for convergence detection. The convergence alert (if triggered) will cover it.

**Exception:** Convergence alerts always send regardless of dedup, since they explicitly represent multi-whale consensus.

---

### 1.8 "Whale of the Week" automated post ✅

Every Sunday at 10:00 UTC, post a brief profile of the top-performing tracked wallet from the past 7 days to both channels.

```
🐋 WHALE OF THE WEEK

gopfan (Tier 1)
Score: 91/100 | Win rate: 78% this week

Last 7 days:
✅ Called YES on [Bitcoin market] @ 18% → resolved 94% (+422%)
✅ Called NO on [Politics market] @ 71% → resolved 8% (+814%)
❌ Called YES on [Sports market] @ 45% → resolved 11%

Total PnL this week: +$42,300
All their trades appear in real-time on Pro channel
```

**Why:** Narrative drives retention. Users become attached to following specific whales, like following traders on social platforms. This is the screenshot content that gets shared on Crypto Twitter.

**Requires:** Trade resolution (1.2) to show outcomes.

---

## P2 — Medium impact, medium effort

### 2.1 Signal score explanation in Pro alerts ✅

Pro users want to know *why* a signal scored what it did. Add a breakdown sent as a follow-up message for signals scoring ≥70:

```
📊 Score Breakdown (82/100)
• Wallet reputation (gopfan, score 91): 32/35
• Trade size: 3.2x avg — ABOVE AVERAGE: 20/25
• Win rate (12/16 trades): 18/20
• Tier 1 wallet: 10/10
• Price conviction (bought at 12%): 10/10 — contrarian play
```

**Implementation:** `_calculate_signal_score()` in `whale_tracker/main.py` already computes all components. Return a breakdown dict alongside the score, then format and send as a follow-up message in `notifier.py`.

This is primarily a refactor + formatting task. No new data needed.

---

### 2.2 Watchlist — let users follow specific markets or keywords ✅

Add a `/watch [keyword]` command. When a whale alert or price move fires for a market whose title contains the keyword, send the user a personal DM.

**Schema:** `watchlists` table already exists in the DB. Just needs the bot command and matching logic.

**Implementation in `notifier.py`:** After sending to Pro/Free channel, query active watchlists whose keyword appears in `market_title.lower()` and DM each matching user.

**Why:** Personalization dramatically improves engagement and perceived value. Users who customize are far less likely to churn.

---

### 2.3 Convergence alert quality improvements ⬜

**a) Show individual wallet scores in convergence alerts** (requires P0.3 to be done first):
```
🐋🐋🐋 CONVERGENCE ALERT
3 whales betting YES on [Market Title]

Traders:
• gopfan (score: 78) — $4,200
• SilverBera (score: 65) — $2,800
• Whale_C (score: 52) — $1,100

Combined: $8,100 | Signal: 74/100
```

**b) Track convergence outcomes:** When a convergence-alerted market resolves, log whether the dominant side won. Show in weekly digest: "Our last 5 convergence signals: ✅✅✅❌✅"

**c) Lower the convergence threshold to 2 wallets but require ≥$10k combined.** Current: 2+ wallets, 60% directional agreement. Result: very few convergence alerts since most wallets are inactive. With refreshed wallets (P0.1), fine-tune this threshold based on actual data.

---

### 2.4 Monthly signal scorecard for Pro subscribers ⬜

Send Pro subscribers a "your month in whales" card in the first week of each month:
- Signals received: X
- Markets resolved: Y of those
- Win rate on signals ≥60: Z%
- Biggest winning call: [market], +X%
- "These signals moved $Xk before the market moved"

**Why:** Reinforces perceived value just before the next Stripe renewal date. Strongest anti-churn mechanism that doesn't require any new features — just surfacing the data already being collected.

**Requires:** Trade resolution (1.2).

---

### 2.5 Annual pricing option ✅

Add a `/subscribe annual` option at $249/year (~$20.75/mo, 28% discount vs monthly).

```
Choose your plan:
• Monthly — $29/mo  → [Subscribe Monthly]
• Annual  — $249/yr → [Subscribe Annual] (save 28%)
```

**Why:** Annual subscribers churn almost never. The discount pays for itself in retention within 2 months. Stripe supports this natively with a separate Price ID.

**Technical:** Add `STRIPE_ANNUAL_PRICE_ID` to `config/.env` (env var already referenced in `subscribe_bot.py` as an optional env var). Branch in `subscribe_bot.py` on the user's plan choice.

---

### 2.6 Referral program ✅

Add `/refer` command that generates a unique referral link. When a referred user subscribes:
- Referrer gets 1 month free (Stripe coupon)
- New subscriber gets 20% off first month

**Schema:** `referrals` table already exists in `payments_db.py` with `referrer_id, referred_id, status, rewarded_at` columns. Just needs the Stripe coupon logic and bot command.

**Why:** Polymarket users are concentrated in communities (Crypto Twitter, prediction market Discord). Peer referrals convert at 3–5x the rate of organic discovery.

---

### 2.7 Category performance leaderboard (weekly) ✅

Add to the weekly digest (or as a standalone Monday message to Pro):

```
📊 Category Performance This Week

🏆 Crypto: 8/10 signals correct (80%)
🥈 Politics: 6/9 (67%)
🥉 Sports: 4/7 (57%)
⚠️  Finance: 2/5 (40%) — approach with caution
```

**Why:** Helps subscribers weight signals appropriately. Provides editorial insight that differentiates WhalePulse from a raw data feed. Also communicates that the product self-audits.

**Requires:** Trade resolution (1.2). `trades.category` and `trades.won` are the only fields needed.

---

### 2.8 Post to both channels when an alerted market resolves ✅

When a market that was previously alerted resolves, send a resolution summary to both channels:

```
✅ MARKET RESOLVED — Whale was RIGHT

[Market Title]
Whale gopfan bought YES @ 18% on Mar 10
Final outcome: YES ✅ (resolved 94%)

WhalePulse called it 4 days early.
Score was 82/100.
```

**Why:** This is the single most powerful retention and conversion message. It closes the loop on the value prop in real time, visible to both paying and free users. Free users see the outcome of a signal they weren't allowed to act on — maximum FOMO.

**Requires:** Trade resolution (1.2). Requires checking `alerted_paid` and `alerted_free` flags in `trades` to know which markets to announce.

---

### 2.9 `/history` command for Pro subscribers ✅

Add `/history [7|30|90]` to Pro subscribers only:

```
/history 7
→ Last 7 days: 34 alerts | 22 resolved | 16 correct (73%)
Top signals: [market1], [market2], [market3]
```

**Implementation:** Query `alert_log` and `trades` tables — data already exists. This is a query + formatting task.

---

## P3 — Higher effort, high upside (after P0/P1 are solid)

### 3.1 Price move + whale correlation alert ⬜

**The insight:** The scanner detects ≥10% price moves, and the tracker knows which wallets recently traded those markets. If a whale bought a market and it subsequently moved ≥10%, that's confirmation of the signal. Alerting on this correlation is a new and distinct signal type.

**Implementation:**
1. When scanner detects a ≥10% move on market slug X, query `trades` for any trades on slug X with `timestamp > now - 48h`
2. If found: send a "WHALE CALLED IT" alert to Pro channel with the original trade details + price move confirmation
3. Log this as `alert_type='whale_confirmation'` in `alert_log`

**Why:** This closes the feedback loop in real time and provides the most compelling pro-upgrade content possible.

---

### 3.2 Discord delivery option ⬜

Many Polymarket power users are primarily on Discord. Add optional Discord webhook delivery:

1. `/settings discord [webhook_url]` — user pastes their Discord webhook
2. Mirror alerts to their webhook in parallel with Telegram
3. Store in a `user_settings` table: `(telegram_id, discord_webhook, ...)`

**Why:** Opens a new acquisition channel (Discord servers) and reduces churn for users who are more active on Discord than Telegram.

---

### 3.3 Wallet discovery transparency posts ⬜

Every time a new Tier 2 wallet is discovered and added to tracking, post to Pro channel:

```
🔍 NEW WHALE DISCOVERED
Wallet: [pseudonym]
Why flagged: Traded same 3 markets as gopfan within 12h
Total trades observed: 12 | Est. PnL: +$18,400
We're watching closely...
```

**Why:** Communicates the intelligence behind the product and creates story continuity — subscribers become invested in following new discoveries. This makes WhalePulse feel like a living research team rather than a static script.

---

### 3.4 Personal wallet tracker — user links their own Polymarket account ⬜

Allow a Pro subscriber to link their own Polymarket wallet address:

```
/track_me 0xabc123...
```

WhalePulse then monitors their wallet like any other, but sends personalized DMs:
- "A whale you follow (gopfan) just entered the same market you're in"
- "You're on the opposite side of a whale consensus signal — heads up"

**Why:** This is a premium feature that directly ties to the subscriber's own trades. It's the most personalized value a $29/mo product can provide.

---

## Quick wins — small changes, immediate impact

| Idea | Effort | Impact | Status |
|------|--------|--------|--------|
| Add "You're eligible for a free trial" to `/start` message for new users | 30 min | High | ✅ |
| Add total subscriber count to daily digest ("Join X Pro members") | 15 min | Medium | ✅ |
| Show `expires_at` date in `/account` so users know when to renew | 15 min | Low | ✅ |
| Post to both channels when a previously alerted market resolves | 1h | High | ✅ |
| Add emoji category icons to price move alerts (🇺🇸 politics, ₿ crypto, 🏀 sports) | 30 min | Low | ✅ |
| On `/subscribe`, show current Pro member count for social proof | 15 min | Medium | ✅ |
| Send a "welcome to Pro" message on day 1 explaining what each alert type means | 1h | Medium | ✅ |
| Add `/feedback` command that DMs admin | 30 min | Medium | ✅ |
| Filter alerts for already-resolved markets (don't alert on a market that closed 2h ago) | 30 min | High | ✅ |
| Log when free-tier trades are dropped due to score < 60 (currently silent) | 15 min | Low | ✅ |

---

## Metrics to track

None of these are currently measured. Start now so you know what's working:

1. **Free → Pro conversion rate**: `subscribers` table has cohort data. Track month-joined vs. month-subscribed.
2. **Signal accuracy rate**: `(resolved trades where won=1 and signal_score >= 60) / total resolved` — requires trade resolution (1.2).
3. **Alert volume per day**: Are you sending enough alerts to justify $29/mo? Track `alert_log` row count per day.
4. **Trial conversion rate**: Once trials launch (1.4), track `status='trial'` → `status='active'` funnel.
5. **Churn reason**: Add a one-question prompt when `/cancel` is run before completing cancellation.
6. **Convergence signal win rate**: Track separately from regular trade signals — this is your highest-value signal type.

---

## Completed

### ✅ Free-tier delay bug fix (2026-03-13)
`_send_trade_alerts()` was only called when new trades were detected in the same polling cycle. Trades that hit the 30-minute delay window during a quiet period were never sent. Fixed by calling `_send_trade_alerts()` unconditionally every 3-minute cycle.

---

## Implementation order (recommended)

```
Week 1: P0.1 → P0.2 → P0.3           (get alerts flowing; fix revenue)
Week 2: 1.4 (trial) → 1.1 (nudges)   (start converting free users)
Week 3: 1.2 (trade resolution)        (unlock accuracy story)
Week 4: 1.3 (/account misses) → 1.6 (market close dates) → 1.8 (whale of week)
Month 2: P2 features, starting with 2.8 (resolution posts) and 2.4 (monthly scorecard)
Month 3: P3 features after retention baseline is solid
```

**The core loop to optimize:** Free user joins → sees alerts → sees what they missed → tries trial → converts → sees whale of week → stays.

Every feature should be evaluated against whether it strengthens one step in that loop.

---

## Notes for the engineer

- P0.1 (wallet refresh): The existing `SEED_WALLETS` constant in `whale_tracker/main.py` is straightforward to update. The harder part is rewriting `discover_whales()` to query live Polymarket data.
- P0.2 (service deployment): Purely ops — no code changes needed to either bot, just systemd unit files.
- P0.3 (convergence names): Add `wallet_names TEXT` column to `convergence_events` in `init_db()`. Since it uses `CREATE TABLE IF NOT EXISTS`, the column won't be added automatically on restart — you'll need `ALTER TABLE convergence_events ADD COLUMN wallet_names TEXT` or wipe and re-initialize the DB.
- 1.2 (trade resolution): Use `condition_id` to look up market outcomes on Polymarket — confirmed as the correct lookup key. Hook into the existing 4h `update_scores()` scheduler.
- 1.4 (trial): `used_trial` and `expires_at` columns already exist in `payments_db.py`. `subscription_checker.py` can handle trial expiry with a `status='trial'` check.
- 1.6 (market close date): The `end_date` field is in Polymarket's market API response. Store it in the `trades` table or look it up from `polymarket_api.py:get_market_by_condition()` at alert time.
- 1.7 (dedup): Check `alert_log` before sending rather than adding a new table.
- 2.5 (annual pricing): `STRIPE_ANNUAL_PRICE_ID` is already referenced in `subscribe_bot.py` as an optional env var — just needs to be set in `config/.env`.
- 2.6 (referral): `referrals` table already exists. Needs Stripe coupon creation API call.
- 3.4 (personal wallet): Requires new `user_wallets` table and a hook in `whale_tracker/main.py` to cross-reference user wallets against active trades.
