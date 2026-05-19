# WhalePulse 🐋

> Real-time Polymarket whale tracker delivered via Telegram, with a free/Pro subscription tier backed by Stripe.
>
> [![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
> [![Telegram](https://img.shields.io/badge/Telegram-Bot-26A5E4?style=flat-square&logo=telegram&logoColor=white)](https://core.telegram.org/bots)
> [![Stripe](https://img.shields.io/badge/Stripe-Payments-635BFF?style=flat-square&logo=stripe&logoColor=white)](https://stripe.com)
> [![Polymarket](https://img.shields.io/badge/Polymarket-API-00B4D8?style=flat-square)](https://polymarket.com)
>
> ---
>
> ## What It Does
>
> WhalePulse monitors high-value ("whale") traders on [Polymarket](https://polymarket.com) and sends real-time trade alerts to subscribers via Telegram. Alerts are scored and filtered by a signal engine, then split across two tiers:
>
> - **Free channel** — delayed 30 min, high-confidence signals only (score ≥ 40)
> - - **Pro channel** (paid via Stripe) — real-time alerts, lower threshold (score ≥ 30), full market data and convergence alerts
>  
>   - A second bot monitors Polymarket for large price moves (≥10% on high-volume markets) and sends those as separate scanner alerts.
>  
>   - ---
>
> ## Architecture
>
> Six independent Python processes share a common `shared/` library and a single SQLite database.
>
> | Service | File | Purpose | Schedule |
> |---------|------|---------|----------|
> | `whalepulse-tracker` | `bots/whale_tracker/main.py` | Core engine: polls wallets, scores trades, sends alerts | Every 3 min (trades), 4h (scoring), 6h (discovery) |
> | `whalepulse-scanner` | `bots/scanner/main.py` | Detects ≥10% price moves on markets with ≥$50k 24h volume | Every 15 min |
> | `whalepulse-subscribe` | `bots/payments/subscribe_bot.py` | Subscription commands + admin commands (`/status`, `/whales`, `/trades`, `/costs`) | On-demand (polling) |
> | `whalepulse-payments` | `bots/payments/webhook_server.py` | Stripe webhook server — generates single-use Pro channel invite links | Continuous (port 80) |
> | `whalepulse-subcheck` | `bots/payments/subscription_checker.py` | Removes expired paid and trial subscribers from Pro channel | Every 1h |
> | `whalepulse-ai-admin` | `bots/ai_admin/main.py` | AI admin bridge — Telegram → Claude → live file edits + bash on server | On-demand (polling) |
>
> ### Scheduled Digests (whale_tracker)
>
> - **Daily digest** — 09:00 UTC
> - - **Weekly summary** — Monday 09:00 UTC
>   - - **Whale of the Week** — Sunday 10:00 UTC
>     - - **Category leaderboard** — Sunday 18:00 UTC
>      
>       - ---
>
> ## Signal Score (0–100)
>
> Every trade is assigned a signal score composed of:
>
> | Component | Max Points |
> |-----------|-----------|
> | Wallet reputation | 35 |
> | Trade size vs. wallet average | 25 |
> | Historical win rate | 20 |
> | Trader tier (known / discovered) | 10 |
> | Price conviction at extremes | 10 |
>
> Alerts are sent when `signal_score ≥ 30` (Pro) or `signal_score ≥ 40` (Free, 30 min delayed).
>
> **Convergence alerts** trigger when ≥2 tracked wallets take the same directional position with ≥60% agreement and `signal_score ≥ 40`.
>
> ---
>
> ## Project Structure
>
> ```
> whalepulse/
> ├── bots/
> │   ├── whale_tracker/
> │   │   └── main.py          # Core polling engine
> │   ├── scanner/
> │   │   └── main.py          # Price movement scanner
> │   ├── payments/
> │   │   ├── subscribe_bot.py       # User-facing subscription bot + admin
> │   │   ├── webhook_server.py      # Stripe webhook handler
> │   │   └── subscription_checker.py # Expired subscriber cleanup
> │   ├── ai_admin/
> │   │   └── main.py          # AI-powered admin Telegram bridge
> │   └── admin/               # (disabled — merged into subscribe_bot.py)
> ├── shared/
> │   ├── database.py          # SQLite interface (WAL, FK enabled)
> │   ├── payments_db.py       # Stripe subscription records
> │   ├── polymarket_api.py    # Async Polymarket HTTP client (0.3s rate limit)
> │   ├── ai_client.py         # Claude API wrapper with prompt-hash caching
> │   └── notifier.py          # Telegram message formatting and sending
> ├── systemd/                 # Systemd service unit files
> ├── docs/                    # Engineer log, ideas, issues
> ├── .claude/                 # Claude Code project memory
> ├── bootstrap.py             # One-time project generator (not for regular use)
> ├── bootstrap_payments.py    # One-time payments module generator
> └── CLAUDE.md                # Claude Code session context
> ```
>
> ---
>
> ## Setup
>
> ### Prerequisites
>
> - Python 3.10+
> - - A Linux server with `systemd`
>   - - Telegram bot tokens (two: one for main bots, one for AI admin)
>     - - Stripe account with a subscription product configured
>       - - Anthropic API key (for AI admin + trade analysis)
>        
>         - ### 1. Clone and create virtualenv
>        
>         - ```bash
>           git clone https://github.com/medinahli/whalepulse.git
>           cd whalepulse
>           python3 -m venv venv
>           source venv/bin/activate
>           pip install -r requirements.txt   # generate via bootstrap.py if not present
>           ```
>
> ### 2. Configure environment variables
>
> Create `config/.env`:
>
> ```env
> # Telegram
> TELEGRAM_BOT_TOKEN=
> TELEGRAM_CHAT_ID=
> TELEGRAM_PRO_CHANNEL_ID=
> TELEGRAM_FREE_CHANNEL_ID=
> ADMIN_BOT_TOKEN=          # Must differ from TELEGRAM_BOT_TOKEN
>
> # Claude / Anthropic
> ANTHROPIC_API_KEY=
>
> # Stripe
> STRIPE_SECRET_KEY=
> STRIPE_PRICE_ID=
> STRIPE_WEBHOOK_SECRET=
> DOMAIN=
> WEBHOOK_PORT=80
> ```
>
> ### 3. Install and enable systemd services
>
> ```bash
> sudo cp systemd/*.service /etc/systemd/system/
> sudo systemctl daemon-reload
> sudo systemctl enable --now whalepulse-tracker whalepulse-scanner \
>   whalepulse-payments whalepulse-subscribe \
>   whalepulse-subcheck whalepulse-ai-admin
> ```
>
> ---
>
> ## Managing Services
>
> ```bash
> # Check status of all services
> sudo systemctl status whalepulse-tracker whalepulse-scanner \
>   whalepulse-payments whalepulse-subscribe \
>   whalepulse-subcheck whalepulse-ai-admin
>
> # Restart a specific service
> sudo systemctl restart whalepulse-tracker
>
> # View live logs
> journalctl -u whalepulse-tracker -f
> ```
>
> > **Note:** `whalepulse-admin` is **disabled** — all admin commands were merged into `whalepulse-subscribe` with `_is_admin()` access control.
> >
> > ---
> >
> > ## Payment Flow
> >
> > 1. User sends `/subscribe` to the Telegram bot
> > 2. 2. Bot generates a Stripe Checkout session link
> >    3. 3. Stripe fires webhook → `webhook_server.py` (port 80)
> >       4. 4. On successful payment, a single-use 24h Telegram invite link is generated for the Pro channel
> >          5. 5. `subscription_checker.py` runs hourly to remove expired or cancelled subscribers
> >            
> >             6. ---
> >            
> >             7. ## Data & Storage
> >            
> >             8. | Path | Contents |
> > |------|----------|
> > | `data/whalepulse.db` | Main SQLite database (~110 MB with historical data). Tables: `wallets`, `trades`, `positions`, `convergence_events`, `alert_log` |
> > | `data/cache/` | Claude API response cache (1h TTL, keyed by prompt hash) |
> > | `data/api_costs.json` | Running Claude API cost log |
> > | `logs/` | Service output logs |
> > | `memory/` | Claude Code session memory (`user.md`, `preferences.md`, `decisions.md`, `people.md`) |
> > | `config/.env` | Environment variables (gitignored) |
> >
> > All paths under `data/`, `logs/`, `memory/`, and `config/.env` are gitignored.
> >
> > ---
> >
> > ## AI Admin Bot
> >
> > The `whalepulse-ai-admin` service exposes a private Telegram bot (separate token) that gives admin users a natural-language interface to manage the server:
> >
> > - Chat with Claude directly from Telegram
> > - - Claude can read/write files and run bash commands on the server
> >   - - Streaming responses with live message updates
> >     - - `/reset` — clear conversation history
> >       - - `/status` — show running services
> >         - - `/logs [service]` — tail recent logs
> >          
> >           - Access is restricted to the configured `ADMIN_USER_ID`.
> >          
> >           - ---
> >
> > ## Key Constants
> >
> > | Constant | Value | Description |
> > |----------|-------|-------------|
> > | `MIN_SIGNAL_PAID` | 30 | Minimum signal score for Pro channel alerts |
> > | `MIN_SIGNAL_FREE` | 40 | Minimum signal score for free channel alerts |
> > | Free alert delay | 30 min | How long Pro-only alerts are held before free tier |
> > | `PRICE_MOVE_PCT` | 10% | Scanner: minimum price move to trigger alert |
> > | `MIN_VOLUME_24H` | $50,000 | Scanner: minimum 24h volume to track a market |
> > | `SCAN_INTERVAL` | 15 min | How often the scanner runs |
> > | Claude cache TTL | 1h | AI response cache duration |
> > | Claude rate limit | 15 calls/min | Self-imposed limit in `ai_client.py` |
