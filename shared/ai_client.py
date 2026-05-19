"""
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
            model="claude-sonnet-4-6", max_tokens=max_tokens,
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
