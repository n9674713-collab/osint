import os
import json
import time
import logging
import threading
from datetime import datetime
import requests
from flask import Flask, jsonify, request
from flask_cors import CORS
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# ================== LOAD ENV ==================
load_dotenv()

BOT_TOKEN     = os.getenv("BOT_TOKEN")
ADMIN_IDS     = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
API_URL       = os.getenv(
    "API_URL",
    "https://geniushacker.vercel.app/api/number/ADITYA-D3-@ADIIX-7WOLDY9P?number={}",
)
FRONTEND_URL  = os.getenv("FRONTEND_URL", "*")
PORT          = int(os.getenv("PORT", 8080))
CACHE_TTL     = int(os.getenv("CACHE_TTL", 86400))   # 24h
SELF_URL      = os.getenv("RENDER_EXTERNAL_URL", os.getenv("SELF_URL", "")).rstrip("/")
PING_INTERVAL = 5 * 60                                # 5 min

USERS_FILE = "users.json"
CACHE_FILE = "cache.json"

# ================== VALIDATION ==================
if not BOT_TOKEN:
    raise SystemExit("❌ BOT_TOKEN environment variable missing hai!")
if not ADMIN_IDS:
    raise SystemExit("❌ ADMIN_IDS environment variable missing hai!")

# ================== LOGGING ==================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("bot")

# ================== FLASK APP ==================
flask_app = Flask(__name__)

# CORS — agar FRONTEND_URL="*" to sab allow, warna comma-separated list
if FRONTEND_URL == "*":
    CORS(flask_app, resources={r"/*": {"origins": "*"}})
else:
    origins = [u.strip() for u in FRONTEND_URL.split(",") if u.strip()]
    CORS(flask_app, resources={r"/*": {"origins": origins}})


# ================== STORAGE HELPERS ==================
def _load(path):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"Save failed ({path}): {e}")


def load_users():    return _load(USERS_FILE)
def save_users(u):   _save(USERS_FILE, u)
def load_cache():    return _load(CACHE_FILE)
def save_cache(c):   _save(CACHE_FILE, c)


def track_user(user):
    users = load_users()
    uid = str(user.id)
    now = datetime.now().strftime("%d-%m-%Y %H:%M:%S")
    is_new = uid not in users
    users[uid] = {
        "name":       user.full_name,
        "username":   user.username or "",
        "first_seen": users.get(uid, {}).get("first_seen", now),
        "last_seen":  now,
    }
    save_users(users)
    return is_new


def get_cached(number):
    cache = load_cache()
    entry = cache.get(number)
    if not entry:
        return None
    if time.time() - entry.get("ts", 0) > CACHE_TTL:
        cache.pop(number, None)
        save_cache(cache)
        return None
    return entry.get("data")


def set_cached(number, data):
    cache = load_cache()
    cache[number] = {"ts": time.time(), "data": data}
    save_cache(cache)


def cache_stats():
    cache = load_cache()
    now = time.time()
    valid = sum(1 for v in cache.values() if now - v.get("ts", 0) <= CACHE_TTL)
    return len(cache), valid


# ================== JSON CLEANER ==================
def clean_data(obj):
    """youtube & developer hatao, deepl -> @incognito_4041, null hatao"""
    if isinstance(obj, dict):
        new = {}
        for k, v in obj.items():
            lk = k.lower()
            if "youtube" in lk or "developer" in lk:
                continue
            if "deepl" in lk:
                new[k] = "@incognito_4041"
                continue
            cleaned = clean_data(v)
            if cleaned is None or cleaned == "":
                continue
            new[k] = cleaned
        return new
    if isinstance(obj, list):
        return [clean_data(x) for x in obj]
    return obj


# ================== FLASK ROUTES ==================
@flask_app.route("/", methods=["GET"])
def root():
    return jsonify({
        "status": "success",
        "message": "Number Info Bot is running ✅",
        "time": datetime.now().strftime("%d-%m-%Y %H:%M:%S"),
    })


@flask_app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


@flask_app.route("/api/lookup", methods=["POST", "GET"])
def api_lookup():
    """
    Frontend isse call karega.
    POST  { "number": "9876543210" }
    GET   /api/lookup?number=9876543210
    """
    try:
        if request.method == "POST":
            body = request.get_json(silent=True) or {}
            number = str(body.get("number", "")).strip()
        else:
            number = str(request.args.get("number", "")).strip()

        if not number.isdigit() or len(number) != 10:
            return jsonify({"status": "error",
                            "message": "Valid 10-digit number bhejo"}), 400

        # cache check
        cached = get_cached(number)
        if cached is not None:
            return jsonify({"status": "success", "source": "cache", "data": cached})

        # API call
        r = requests.get(API_URL.format(number), timeout=20)
        r.raise_for_status()
        data = clean_data(r.json())
        set_cached(number, data)

        return jsonify({"status": "success", "source": "api", "data": data})

    except requests.exceptions.RequestException:
        logger.exception("API request failed")
        return jsonify({"status": "error",
                        "message": "Data not found ya server busy hai"}), 502
    except Exception:
        logger.exception("Unexpected error")
        return jsonify({"status": "error",
                        "message": "Something went wrong"}), 500


@flask_app.route("/api/stats", methods=["GET"])
def api_stats():
    users = load_users()
    total = len(users)
    today = datetime.now().strftime("%d-%m-%Y")
    today_count = sum(1 for u in users.values() if u.get("first_seen", "").startswith(today))
    cache_total, cache_valid = cache_stats()
    return jsonify({
        "total_users": total,
        "today_new":   today_count,
        "cache_total": cache_total,
        "cache_valid": cache_valid,
    })


# ================== KEEP-ALIVE (Render free tier) ==================
def keep_alive():
    """Har 5 min me khud ko ping karega — Render free tier me bot sota nahi."""
    time.sleep(30)  # server start hone do
    while True:
        try:
            if SELF_URL:
                r = requests.get(SELF_URL + "/health", timeout=10)
                logger.info(f"🔄 Self-ping {SELF_URL}/health -> {r.status_code}")
        except Exception as e:
            logger.warning(f"Self-ping failed: {e}")
        time.sleep(PING_INTERVAL)


# ================== ADMIN NOTIFY ==================
async def notify_admin(context: ContextTypes.DEFAULT_TYPE, user, event="started the bot"):
    now = datetime.now().strftime("%d-%m-%Y %H:%M:%S")
    text = (
        "🆕 *New User Alert*\n\n"
        f"👤 *Name:* {user.full_name}\n"
        f"🔗 *Username:* @{user.username if user.username else 'N/A'}\n"
        f"🆔 *ID:* `{user.id}`\n"
        f"⏰ *Time:* {now}\n"
        f"📌 *Event:* {event}"
    )
    for aid in ADMIN_IDS:
        try:
            await context.bot.send_message(chat_id=aid, text=text, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Admin notify failed for {aid}: {e}")


# ================== MENU ==================
def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📞 Number Info", callback_data="number")]
    ])


# ================== BOT HANDLERS ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if track_user(user):
        await notify_admin(context, user, event="started the bot")

    await update.message.reply_text(
        "🔓 NUMBER TO INFO BOT 🔓\n\nChoose a tool from the menu below:",
        reply_markup=main_menu_keyboard(),
    )


async def handle_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    track_user(query.from_user)
    context.user_data["api_type"] = query.data
    await query.edit_message_text(
        "📞 Send the **mobile number** (10 digits):",
        parse_mode="Markdown",
    )


async def process_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    api_type = context.user_data.get("api_type")
    if not api_type:
        await update.message.reply_text("⚠️ Please /start karo aur option choose karo.")
        return

    number = update.message.text.strip()
    if not number.isdigit() or len(number) != 10:
        await update.message.reply_text("❌ Valid 10-digit mobile number bhejo.")
        return

    cached = get_cached(number)
    if cached is not None:
        pretty = json.dumps(cached, indent=2, ensure_ascii=False)
        if len(pretty) > 4000:
            pretty = pretty[:4000] + "\n... (truncated)"
        await update.message.reply_text(
            f"⚡ *From Cache:*\n```json\n{pretty}\n```",
            parse_mode="Markdown",
        )
        context.user_data.pop("api_type", None)
        return

    msg = await update.message.reply_text("⏳ Fetching data, please wait...")
    try:
        r = requests.get(API_URL.format(number), timeout=20)
        r.raise_for_status()
        cleaned = clean_data(r.json())
        set_cached(number, cleaned)

        pretty = json.dumps(cleaned, indent=2, ensure_ascii=False)
        if len(pretty) > 4000:
            pretty = pretty[:4000] + "\n... (truncated)"

        await msg.edit_text(f"✅ **Result:**\n```json\n{pretty}\n```",
                            parse_mode="Markdown")
    except Exception:
        logger.exception("API request failed")
        await msg.edit_text("❌ Data not found ya server busy hai. Thodi der baad try karo.")

    context.user_data.pop("api_type", None)


# ================== ADMIN PANEL ==================
def is_admin(uid): return uid in ADMIN_IDS


def admin_panel_text():
    users = load_users()
    total = len(users)
    today = datetime.now().strftime("%d-%m-%Y")
    today_count = sum(1 for u in users.values() if u.get("first_seen", "").startswith(today))
    ct, cv = cache_stats()
    return (
        "🛠 *ADMIN PANEL*\n\n"
        f"👥 Total Users  : *{total}*\n"
        f"📅 Today New    : *{today_count}*\n"
        f"💾 Cache Entries: *{ct}* (valid: {cv})\n"
        f"⏱ Cache TTL     : *{CACHE_TTL // 3600} hours*\n"
    )


def admin_panel_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Users List", callback_data="admin_users")],
        [InlineKeyboardButton("🗑 Clear Cache", callback_data="admin_clearcache")],
        [InlineKeyboardButton("🔄 Refresh",     callback_data="admin_refresh")],
    ])


async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Aap admin nahi ho.")
        return
    await update.message.reply_text(admin_panel_text(),
                                    parse_mode="Markdown",
                                    reply_markup=admin_panel_kb())


async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Access denied", show_alert=True)
        return
    await query.answer()

    if query.data == "admin_refresh":
        await query.edit_message_text(admin_panel_text(),
                                      parse_mode="Markdown",
                                      reply_markup=admin_panel_kb())

    elif query.data == "admin_clearcache":
        save_cache({})
        await query.edit_message_text("✅ Cache clear ho gaya.\n\n" + admin_panel_text(),
                                      parse_mode="Markdown",
                                      reply_markup=admin_panel_kb())

    elif query.data == "admin_users":
        users = load_users()
        if not users:
            await query.edit_message_text("📭 Koi user nahi mila.")
            return
        items = sorted(users.items(), key=lambda x: x[1].get("last_seen", ""), reverse=True)
        lines = ["📋 *Recent Users (Top 30)*\n"]
        for uid, info in items[:30]:
            uname = f"@{info['username']}" if info.get("username") else "N/A"
            lines.append(
                f"👤 {info.get('name', 'N/A')}\n"
                f"   🔗 {uname}\n"
                f"   🆔 `{uid}`\n"
                f"   🕐 {info.get('last_seen', 'N/A')}\n"
            )
        text = "\n".join(lines)
        if len(text) > 4000:
            text = text[:4000] + "\n... (truncated)"
        back = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="admin_refresh")]])
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=back)


# ================== BOT RUNNER (background thread) ==================
def run_bot():
    try:
        application = Application.builder().token(BOT_TOKEN).build()

        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("admin", admin_cmd))
        application.add_handler(CallbackQueryHandler(handle_choice, pattern="^number$"))
        application.add_handler(CallbackQueryHandler(admin_callback, pattern="^admin_"))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_query))

        logger.info("🤖 Bot polling started...")
        # stop_signals=None zaroori hai kyunki ye thread me chal raha hai
        application.run_polling(allowed_updates=Update.ALL_TYPES, stop_signals=None)
    except Exception:
        logger.exception("Bot crashed!")


# ================== MAIN ==================
def main():
    # 1) bot thread
    threading.Thread(target=run_bot, daemon=True).start()
    # 2) keep-alive thread
    threading.Thread(target=keep_alive, daemon=True).start()
    # 3) flask main thread (Render expects web service)
    logger.info(f"🌐 Flask starting on 0.0.0.0:{PORT}")
    flask_app.run(host="0.0.0.0", port=PORT, threaded=True)


if __name__ == "__main__":
    main()