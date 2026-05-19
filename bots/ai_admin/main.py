#!/usr/bin/env python3
"""
WhalePulse AI Admin Bot
Telegram bot that bridges admin natural-language commands to Claude.
Claude can read/write files and run bash commands on the server.

REQUIRES a separate ADMIN_BOT_TOKEN in config/.env — do NOT reuse
TELEGRAM_BOT_TOKEN (that is used by subscribe_bot.py and would cause
a polling conflict).
"""
import sys, os, re, logging, subprocess, textwrap, traceback, time
from pathlib import Path
from dotenv import load_dotenv

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / "config" / ".env")

import anthropic
from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_FILE = ROOT / "logs" / "ai-admin.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
ADMIN_BOT_TOKEN   = os.getenv("ADMIN_BOT_TOKEN", "")
ADMIN_USER_ID     = int(os.getenv("ADMIN_USER_ID") or os.getenv("TELEGRAM_CHAT_ID") or "0")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

if not ADMIN_BOT_TOKEN:
    sys.exit("ADMIN_BOT_TOKEN not set in config/.env")

client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

# ── Conversation history ───────────────────────────────────────────────────────
MAX_HISTORY = 30
history: list[dict] = []

# ── System prompt ─────────────────────────────────────────────────────────────
def _file_tree(root: Path, indent: int = 0) -> str:
    SKIP = {"venv", "__pycache__", ".git", "data", "logs", ".pytest_cache"}
    lines = []
    try:
        entries = sorted(root.iterdir(), key=lambda p: (p.is_file(), p.name))
    except PermissionError:
        return ""
    for entry in entries:
        if entry.name in SKIP or entry.name.endswith(".pyc"):
            continue
        prefix = "  " * indent
        if entry.is_dir():
            lines.append(f"{prefix}{entry.name}/")
            lines.append(_file_tree(entry, indent + 1))
        else:
            lines.append(f"{prefix}{entry.name}")
    return "\n".join(filter(None, lines))


def build_system_prompt() -> str:
    try:
        claude_md = (ROOT / "CLAUDE.md").read_text()
    except Exception:
        claude_md = "(CLAUDE.md not found)"

    tree = _file_tree(ROOT)

    return textwrap.dedent(f"""\
        You're the engineer who built WhalePulse and knows every corner of it.
        You're talking to the owner over Telegram — keep it casual and direct, like texting a friend.

        Tone rules:
        - Short replies unless detail is actually needed. Don't pad.
        - No "Certainly!", no bullet-point walls, no corporate speak.
        - Talk like a human: "yeah", "looks like", "btw", "gimme a sec", "done", "heads up".
        - If something is risky, just say so plainly: "this'll restart the tracker for a few seconds, that ok?"
        - If you notice something related while fixing something, mention it briefly.
        - When you fix something, say what you did in one line, not a paragraph.

        You have full access to the production server. When asked to change something, just do it.

        ## Making changes

        Write files with:
        <file path="relative/path/from/project/root">
        COMPLETE file content here
        </file>

        Run shell commands with:
        <bash>
        command here
        </bash>

        Rules:
        - Always write the COMPLETE file, not a diff.
        - Use relative paths from /home/botrunner/whalepulse/.
        - After touching a running service, restart it with a <bash> block.
        - For read-only stuff (logs, db queries, status) just use <bash>.
        - Keep commands safe — this is prod.

        ## Project root
        /home/botrunner/whalepulse/

        ## CLAUDE.md
        {claude_md}

        ## File tree
        ```
        {tree}
        ```

        ## Services
        whalepulse-tracker, whalepulse-scanner, whalepulse-payments,
        whalepulse-subscribe, whalepulse-subcheck, whalepulse-ai-admin

        ## Database
        SQLite at data/whalepulse.db — shared/database.py and shared/payments_db.py
    """)


SYSTEM_PROMPT = build_system_prompt()

# ── Action execution ──────────────────────────────────────────────────────────
FILE_RE = re.compile(r'<file\s+path=["\']([^"\']+)["\']\s*>(.*?)</file>', re.DOTALL)
BASH_RE = re.compile(r'<bash>(.*?)</bash>', re.DOTALL)


def apply_file(rel_path: str, content: str) -> str:
    target = ROOT / rel_path.strip()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content.lstrip("\n"))
    log.info("Wrote %s (%d chars)", rel_path, len(content))
    return f"✏️ {rel_path}"


def run_bash(command: str) -> str:
    cmd = command.strip()
    log.info("bash: %s", cmd)
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=60, cwd=str(ROOT)
        )
        out = (result.stdout + result.stderr).strip() or f"(exit {result.returncode})"
        if len(out) > 2000:
            out = out[:1800] + f"\n…(truncated)"
        return f"$ {cmd}\n{out}"
    except subprocess.TimeoutExpired:
        return f"$ {cmd}\n(timed out after 60s)"
    except Exception as e:
        return f"$ {cmd}\n(error: {e})"


def process_ai_response(text: str) -> tuple[str, list[str]]:
    """Execute <file> and <bash> blocks. Returns (cleaned_text, action_results)."""
    action_log: list[str] = []
    working = text

    for match in FILE_RE.finditer(text):
        rel_path, content = match.group(1), match.group(2)
        status = apply_file(rel_path, content)
        action_log.append(status)
        working = working.replace(match.group(0), f"[wrote {rel_path}]", 1)

    def _run(m):
        out = run_bash(m.group(1))
        action_log.append(out)
        return f"```\n{out}\n```"

    working = BASH_RE.sub(_run, working)
    return working.strip(), action_log


# ── Auth ──────────────────────────────────────────────────────────────────────
def _is_admin(update: Update) -> bool:
    return update.effective_user is not None and update.effective_user.id == ADMIN_USER_ID


# ── Streaming helper ──────────────────────────────────────────────────────────
STREAM_UPDATE_INTERVAL = 1.5   # seconds between Telegram edits
STREAM_MIN_NEW_CHARS   = 40    # don't edit unless at least this many new chars


async def _stream_to_message(msg, stream) -> str:
    """
    Stream text from Claude and progressively update a Telegram message.
    Returns the full collected text.
    """
    collected = ""
    last_update_time = 0.0
    last_update_len  = 0

    async for chunk in stream.text_stream:
        collected += chunk
        now = time.monotonic()
        new_chars = len(collected) - last_update_len
        if now - last_update_time >= STREAM_UPDATE_INTERVAL and new_chars >= STREAM_MIN_NEW_CHARS:
            try:
                # Show a cursor so it's obvious it's still typing
                await msg.edit_text(collected[:4000] + " ▌")
                last_update_time = now
                last_update_len  = len(collected)
            except Exception:
                pass  # rate limit or identical text — fine, keep going

    return collected


# ── Handlers ──────────────────────────────────────────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        uid = update.effective_user.id if update.effective_user else "?"
        log.warning("Rejected message from uid %s", uid)
        return

    user_text = (update.message.text or "").strip()
    if not user_text:
        return

    log.info("← %s", user_text[:120])

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    placeholder = await update.message.reply_text("...")

    history.append({"role": "user", "content": user_text})
    while len(history) > MAX_HISTORY:
        history.pop(0)

    try:
        async with client.messages.stream(
            model="claude-opus-4-6",
            max_tokens=8192,
            thinking={"type": "enabled", "budget_tokens": 5000},
            system=[{
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=list(history),
        ) as stream:
            ai_text = await _stream_to_message(placeholder, stream)

        history.append({"role": "assistant", "content": ai_text})
        while len(history) > MAX_HISTORY:
            history.pop(0)

        # Execute any <file> / <bash> blocks
        cleaned, actions = process_ai_response(ai_text)
        reply = cleaned or "(no response)"

        # Final edit — remove cursor, show complete response
        chunks = [reply[i:i+4000] for i in range(0, max(len(reply), 1), 4000)]
        await placeholder.edit_text(chunks[0])
        for chunk in chunks[1:]:
            await update.message.reply_text(chunk)

        # Show action output if any
        if actions:
            action_text = "\n\n".join(actions)
            for i in range(0, len(action_text), 4000):
                await update.message.reply_text(
                    f"```\n{action_text[i:i+4000]}\n```",
                    parse_mode=ParseMode.MARKDOWN,
                )

        log.info("→ %d chars, %d actions", len(reply), len(actions))

    except Exception as e:
        tb = traceback.format_exc()
        log.error("Error: %s\n%s", e, tb)
        short_tb = tb[-600:]
        try:
            await placeholder.edit_text(f"💥 {e}\n\n```\n{short_tb}\n```", parse_mode=ParseMode.MARKDOWN)
        except Exception:
            await placeholder.edit_text(f"Error: {e}")


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    history.clear()
    log.info("History cleared")
    await update.message.reply_text("cleared")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    services = [
        "whalepulse-tracker", "whalepulse-scanner", "whalepulse-payments",
        "whalepulse-subscribe", "whalepulse-subcheck", "whalepulse-ai-admin",
    ]
    lines = []
    for svc in services:
        r = subprocess.run(["systemctl", "is-active", svc], capture_output=True, text=True)
        state = r.stdout.strip()
        icon = "✅" if state == "active" else "❌"
        lines.append(f"{icon} {svc.replace('whalepulse-', '')}: {state}")
    await update.message.reply_text("\n".join(lines))


async def cmd_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Usage: /logs [tracker|scanner|payments|subscribe|subcheck|admin]"""
    if not _is_admin(update):
        return
    svc_map = {
        "tracker": "tracker", "scanner": "scanner", "payments": "payments",
        "subscribe": "subscribe", "subcheck": "subcheck", "admin": "ai-admin",
    }
    key = (context.args[0].lower() if context.args else "tracker")
    log_key = svc_map.get(key, key)

    # prefer stderr (errors), fall back to stdout
    for suffix in ("stderr", "stdout"):
        log_path = ROOT / "logs" / f"{log_key}-{suffix}.log"
        if log_path.exists():
            break
    else:
        await update.message.reply_text(f"no log for '{key}'. options: {', '.join(svc_map)}")
        return

    result = subprocess.run(["tail", "-40", str(log_path)], capture_output=True, text=True)
    out = result.stdout.strip() or "(empty)"
    await update.message.reply_text(f"```\n{out[-3500:]}\n```", parse_mode=ParseMode.MARKDOWN)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    await update.message.reply_text(
        "hey, I'm your WhalePulse engineer. just tell me what you need:\n\n"
        "\"lower the free threshold to 35\"\n"
        "\"show last 10 trades\"\n"
        "\"what's crashing in the tracker logs\"\n"
        "\"add wallet 0x123...\"\n\n"
        "/status — service health\n"
        "/logs [tracker|scanner|payments|subscribe|subcheck|admin]\n"
        "/reset — fresh conversation"
    )


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    log.info("AI Admin starting — admin uid: %d, prompt: %d chars", ADMIN_USER_ID, len(SYSTEM_PROMPT))

    app = ApplicationBuilder().token(ADMIN_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("logs", cmd_logs))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    log.info("polling…")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
