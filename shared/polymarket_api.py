"""
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

def get_market_by_condition(condition_id):
    """Get market info by condition_id — used for resolution checking."""
    result = _get(f"{GAMMA_API}/markets", {"conditionId": condition_id, "limit": 1})
    if isinstance(result, list) and result:
        return result[0]
    return None

def get_market_activity(condition_id, limit=50):
    """Get recent trades for a specific market by condition_id."""
    params = {"conditionId": condition_id, "limit": limit}
    result = _get(f"{DATA_API}/trades", params)
    return result if isinstance(result, list) else []


def get_recent_global_trades(limit=500):
    """Get the most recent trades globally — used for wallet discovery."""
    result = _get(f"{DATA_API}/trades", {"limit": limit})
    return result if isinstance(result, list) else []


def extract_wallet_info(activity_item):
    return {
        "address": activity_item.get("proxyWallet", ""),
        "name": activity_item.get("name", ""),
        "pseudonym": activity_item.get("pseudonym", ""),
        "profile_image": activity_item.get("profileImage", ""),
        "bio": activity_item.get("bio", ""),
    }
