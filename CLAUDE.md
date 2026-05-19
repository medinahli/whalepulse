# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Session Memory Protocol

**At the start of every session**, read these files to restore context:
- `memory/user.md` — who the user is, their environment, skill level
- `memory/preferences.md` — how they like to work and communicate
- `memory/decisions.md` — past architectural and operational decisions
- `memory/people.md` — people involved and their roles

**At the end of every session** (or when significant new context is established), update whichever files changed. Use the date format `YYYY-MM-DD` for new entries in `decisions.md`.

## Project Overview

WhalePulse is a Python-based Telegram alerting system that tracks high-value ("whale") traders on [Polymarket](https://polymarket.com) (a prediction market platform). It sends real-time trade alerts to subscribers via Telegram channels, with a two-tier free/paid model backed by Stripe.

## Managing Services

Use `systemctl` to manage all bots:

```bash
sudo systemctl start|stop|restart|status whalepulse-tracker
sudo systemctl start|stop|restart|status whalepulse-scanner
sudo systemctl start|stop|restart|status whalepulse-payments
sudo systemctl start|stop|restart|status whalepulse-subscribe
sudo systemctl start|stop|restart|status whalepulse-subcheck
sudo systemctl start|stop|restart|status whalepulse-ai-admin
```

Note: `whalepulse-admin` is **disabled** — all admin commands (`/status`, `/whales`, `/trades`, `/costs`) were merged into `whalepulse-subscribe` (subscribe_bot.py) with `_is_admin()` access control.

There is no build step, no test suite, and no linter configured. Dependencies are managed via `venv/`.

## Initial Setup

The `bootstrap.py` and `bootstrap_payments.py` scripts generate the shared modules from embedded strings — they are used for initial project generation, not for regular development.

Environment variables live in `config/.env`. Required keys:
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_PRO_CHANNEL_ID`, `TELEGRAM_FREE_CHANNEL_ID`
- `ADMIN_BOT_TOKEN` — separate bot token for `whalepulse-ai-admin` (must differ from `TELEGRAM_BOT_TOKEN`)
- `ANTHROPIC_API_KEY`
- `STRIPE_SECRET_KEY`, `STRIPE_PRICE_ID`, `STRIPE_WEBHOOK_SECRET`, `DOMAIN`, `WEBHOOK_PORT`

## Architecture

### Multi-Bot Structure

Four independent bots share a common `shared/` library and a single SQLite database at `data/whalepulse.db`.

| Bot | Purpose | Schedule |
|-----|---------|----------|
| `bots/whale_tracker/main.py` | Core engine: polls wallets, scores trades, sends alerts | Every 3 min (trades), 4h (scoring), 6h (discovery), daily digest at 09:00 UTC, weekly Mon 09:00, whale of week Sun 10:00, category board Sun 18:00 |
| `bots/scanner/main.py` | Detects ≥10% price moves on markets with ≥$50k 24h volume | Every 15 min |
| `bots/payments/subscribe_bot.py` | Subscription commands + merged admin commands (`/status`, `/whales`, `/trades`, `/costs`) | On-demand (polling) |
| `bots/payments/webhook_server.py` | Stripe webhook server — listens on port 80 (WEBHOOK_PORT env var) | Continuous |
| `bots/payments/subscription_checker.py` | Kicks expired paid and trial subscribers from Pro channel | Every 1h |
| `bots/ai_admin/main.py` | AI admin bridge — Telegram → Claude → file edits + bash on server | On-demand (polling, ADMIN_BOT_TOKEN) |

### Shared Modules (`shared/`)

- `database.py` — SQLite interface (WAL mode, FK enabled). Tables: `wallets`, `trades`, `positions`, `convergence_events`, `alert_log`
- `payments_db.py` — Subscription records for Stripe integration
- `polymarket_api.py` — Async HTTP client for Polymarket APIs (0.3s rate limit)
- `ai_client.py` — Claude API wrapper with prompt-hash caching (1h TTL, 15 calls/min limit) and cost tracking to `data/api_costs.json`
- `notifier.py` — Telegram message formatting and sending

### Alert Tiers

- **Pro channel** (paid): Real-time alerts for trades with `signal_score ≥ 30` (`MIN_SIGNAL_PAID`)
- **Free channel**: Delayed 30 min, only `signal_score ≥ 40` (`MIN_SIGNAL_FREE`)
- **Convergence alerts**: ≥2 wallets, ≥60% directional agreement, `signal_score ≥ 40`

### Signal Score (0–100)

Calculated per trade from: wallet reputation (0–35) + trade size relative to wallet average (0–25) + historical win rate (0–20) + trader tier known/discovered (5–10) + price conviction at extremes (0–10).

### Payment Flow

Stripe webhooks → `webhook_server.py` (port 80, configurable via `WEBHOOK_PORT`) → updates `payments_db.py` → generates single-use 24h Telegram invite links for the Pro channel. `subscription_checker.py` periodically removes expired subscribers.

## Key Data

- Database: `data/whalepulse.db` (~110 MB, historical data)
- AI response cache: `data/cache/`
- API cost log: `data/api_costs.json`
- Logs: `logs/`
- Systemd service names: `whalepulse-tracker`, `whalepulse-scanner`, `whalepulse-payments`, `whalepulse-subscribe`, `whalepulse-subcheck`, `whalepulse-ai-admin` (`whalepulse-admin` is disabled)
