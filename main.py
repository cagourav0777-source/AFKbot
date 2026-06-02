import os
import time
import re
import logging
import asyncio
import threading
import random
import string
from datetime import datetime
from flask import Flask
from pyrogram import Client, filters, enums, idle
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputMediaPhoto,
    CallbackQuery
)
from motor.motor_asyncio import AsyncIOMotorClient
from pyrogram.errors import PeerIdInvalid, ChatAdminRequired

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Validate required environment variables
def getenv_or_raise(key, cast=str, default=None, required=False):
    val = os.getenv(key, None)
    if val is None or val == "":
        if required:
            raise RuntimeError(f"Environment variable {key} is required but not set")
        return default
    try:
        return cast(val) if cast is not None else val
    except Exception as e:
        raise RuntimeError(f"Failed to cast env {key}: {e}")

BOT_TOKEN = getenv_or_raise("BOT_TOKEN", required=True)
API_ID = getenv_or_raise("API_ID", cast=int, required=True)
API_HASH = getenv_or_raise("API_HASH", required=True)
BOT_USERNAME = getenv_or_raise("BOT_USERNAME", required=True)
MONGODB_URI = getenv_or_raise("MONGODB_URI", required=True)
OWNER_ID = getenv_or_raise("OWNER_ID", cast=int, default=0)
PORT = getenv_or_raise("PORT", cast=int, default=8080)

# Bot start time for uptime calculation
START_TIME = time.time()

# Initialize MongoDB
mongo_client = AsyncIOMotorClient(MONGODB_URI)
db = mongo_client.afk_db
afk_collection = db.afk
users_collection = db.users  # For user stats
groups_collection = db.groups  # For tracking groups
broadcast_collection = db.broadcast_tmp  # For temporary broadcast data
auto_delete_collection = db.auto_delete  # For auto-delete settings and messages

# Helper functions
def get_readable_time(seconds: int) -> str:
    seconds = int(seconds)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    return " ".join(parts)

def generate_random_id(length=8):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

async def add_afk(user_id: int, details: dict):
    if not isinstance(details, dict):
        return
    await afk_collection.update_one(
        {"user_id": user_id},
        {"$set": details},
        upsert=True
    )

async def is_afk(user_id: int):
    data = await afk_collection.find_one({"user_id": user_id})
    if data:
        return True, data
    return False, {}

async def remove_afk(user_id: int):
    await afk_collection.delete_one({"user_id": user_id})

async def add_user(user_id: int, first_name: str = "", username: str = ""):
    """Add or update user info, including total AFK time field."""
    await users_collection.update_one(
        {"user_id": user_id},
        {
            "$set": {
                "first_name": first_name,
                "username": username,
                "last_seen": datetime.utcnow()
            },
            "$setOnInsert": {"total_afk_time": 0}
        },
        upsert=True
    )

async def count_users():
    return await users_collection.count_documents({})

async def count_afk_users():
    return await afk_collection.count_documents({})

async def update_user_afk_time(user_id: int, additional_seconds: int):
    """Add to the total AFK time for a user."""
    await users_collection.update_one(
        {"user_id": user_id},
        {"$inc": {"total_afk_time": additional_seconds}},
        upsert=True
    )

async def get_top_afk_users(limit=10):
    cursor = users_collection.find({"total_afk_time": {"$gt": 0}}).sort("total_afk_time", -1).limit(limit)
    top_users = await cursor.to_list(length=limit)
    return top_users

# Track groups
async def track_group(chat_id: int, chat_title: str):
    await groups_collection.update_one(
        {"chat_id": chat_id},
        {"$set": {
            "title": chat_title,
            "last_active": datetime.utcnow()
        }},
        upsert=True
    )

async def count_groups():
    return await groups_collection.count_documents({})

async def get_all_groups():
    groups = []
    async for group in groups_collection.find({}):
        groups.append(group)
    return groups

# =======================================================================
# Auto-delete feature implementation (Per Group Settings)
# =======================================================================
async def init_group_auto_delete_settings(chat_id: int):
    """Initialize auto-delete settings for a group with default values"""
    settings = await auto_delete_collection.find_one({"chat_id": chat_id})
    if not settings:
        await auto_delete_collection.insert_one({
            "type": "group_settings",
            "chat_id": chat_id,
            "enabled": False,
            "delete_after": 300  # 5 minutes
        })
        logger.info(f"Initialized auto-delete settings for group {chat_id}")

async def is_auto_delete_enabled(chat_id: int):
    settings = await auto_delete_collection.find_one({"chat_id": chat_id})
    if settings:
        return settings.get("enabled", False)
    return False

async def get_auto_delete_time(chat_id: int):
    settings = await auto_delete_collection.find_one({"chat_id": chat_id})
    if settings:
        return settings.get("delete_after", 300)
    return 300

async def toggle_auto_delete(chat_id: int, state: bool = None):
    settings = await auto_delete_collection.find_one({"chat_id": chat_id})
    if not settings:
        await init_group_auto_delete_settings(chat_id)
        settings = await auto_delete_collection.find_one({"chat_id": chat_id})

    if state is None:
        new_state = not settings.get("enabled", False)
    else:
        new_state = bool(state)

    await auto_delete_collection.update_one(
        {"chat_id": chat_id},
        {"$set": {"enabled": new_state}},
        upsert=True
    )
    logger.info(f"Auto-delete toggled to {new_state} for group {chat_id}")
    return new_state

async def set_auto_delete_time(chat_id: int, seconds: int):
    await auto_delete_collection.update_one(
        {"chat_id": chat_id},
        {"$set": {"delete_after": seconds}},
        upsert=True
    )
    minutes = seconds // 60
    logger.info(f"Auto-delete time set to {minutes} minutes for group {chat_id}")
    return seconds

async def track_message_for_deletion(message: Message):
    """Track a message for future deletion based on group settings"""
    if not message or not getattr(message, "chat", None):
        return
    if message.chat.type not in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        return

    chat_id = message.chat.id

    if not await is_auto_delete_enabled(chat_id):
        return

    delete_after = await get_auto_delete_time(chat_id)
    delete_at = time.time() + delete_after

    await auto_delete_collection.insert_one({
        "type": "message",
        "message_id": message.id,
        "chat_id": chat_id,
        "delete_at": delete_at
    })
    logger.debug(f"Tracking message for deletion: {message.id} in chat {chat_id}")

async def auto_delete_loop():
    """Background task to delete expired messages"""
    logger.info("Auto-delete task started")
    while True:
        try:
            current_time = time.time()
            query = {"type": "message", "delete_at": {"$lte": current_time}}
            messages_to_delete = await auto_delete_collection.find(query).to_list(length=None)

            if messages_to_delete:
                logger.info(f"Found {len(messages_to_delete)} messages to delete")

            for msg in messages_to_delete:
                try:
                    await app.delete_messages(msg["chat_id"], msg["message_id"])
                    logger.debug(f"Deleted message: {msg['message_id']} in chat {msg['chat_id']}")
                except Exception as e:
                    logger.debug(f"Failed to delete message {msg.get('message_id')} in {msg.get('chat_id')}: {e}")
                finally:
                    # Remove from tracking regardless of success
                    await auto_delete_collection.delete_one({"_id": msg["_id"]})

            await asyncio.sleep(30)
        except Exception as e:
            logger.error(f"Error in auto-delete loop: {e}")
            await asyncio.sleep(60)

# Helper function to generate auto-delete menu for a group
async def get_auto_delete_menu(chat_id: int):
    settings = await auto_delete_collection.find_one({"chat_id": chat_id})
    if not settings:
        await init_group_auto_delete_settings(chat_id)
        settings = await auto_delete_collection.find_one({"chat_id": chat_id})

    enabled = settings.get("enabled", False)
    delete_after = settings.get("delete_after", 300)
    minutes = delete_after // 60

    status = "🟢 Enabled" if enabled else "🔴 Disabled"

    text = (
        f"🤖 **Auto-Delete Settings for This Group**\n\n"
        f"• Status: {status}\n"
        f"• Delete after: `{minutes} minutes`\n\n"
        "**Set Time (minutes):**"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🟢 Enable", callback_data=f"autodel_enable:{chat_id}"),
            InlineKeyboardButton("🔴 Disable", callback_data=f"autodel_disable:{chat_id}")
        ],
        [
            InlineKeyboardButton("5 min", callback_data=f"autodel_time:300:{chat_id}"),
            InlineKeyboardButton("10 min", callback_data=f"autodel_time:600:{chat_id}")
        ],
        [
            InlineKeyboardButton("30 min", callback_data=f"autodel_time:1800:{chat_id}"),
            InlineKeyboardButton("60 min", callback_data=f"autodel_time:3600:{chat_id}")
        ],
        [
            InlineKeyboardButton("🔙 Back to Main", callback_data="back_to_start"),
            InlineKeyboardButton("❌ Close", callback_data=f"autodel_close:{chat_id}")
        ]
    ])

    return text, keyboard

# =======================================================================
# End of auto-delete feature
# =======================================================================

# Create Flask server for health checks
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return "AFK Bot is running! 🚀", 200

def run_flask():
    flask_app.run(host='0.0.0.0', port=PORT)

# Bot initialization
class Bot(Client):
    def __init__(self):
        super().__init__(
            "afk_bot",
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=BOT_TOKEN,
            in_memory=True
        )

    async def start(self):
        await super().start()
        logger.info("Bot client started successfully")

        # Send startup notification to owner
        if OWNER_ID:
            try:
                me = await self.get_me()
                await self.send_message(
                    OWNER_ID,
                    "✅ AFK Bot Started Successfully!\n"
                    f"🤖 Username: @{BOT_USERNAME}\n"
                    f"🆔 Bot ID: {me.id if me else 'unknown'}"
                )
            except Exception as e:
                logger.error(f"Startup notification failed: {e}")

    async def stop(self):
        await super().stop()
        logger.info("Bot client stopped")

app = Bot()

# Track bot start time for uptime
BOT_START_TIME = time.time()

# Track when bot is added to a group
@app.on_message(filters.new_chat_members)
async def new_chat_members(_, message: Message):
    if not message or not message.new_chat_members:
        return
    me = await app.get_me()
    for member in message.new_chat_members:
        if member.id == me.id:
            await track_group(message.chat.id, message.chat.title)
            logger.info(f"Bot added to group: {message.chat.title} ({message.chat.id})")
            await init_group_auto_delete_settings(message.chat.id)

# Start command handler with new image and message
@app.on_message(filters.command(["start", "help"]))
async def start_command(_, message: Message):
    user = message.from_user
    if not user:
        return

    uptime = get_readable_time(int(time.time() - BOT_START_TIME))

    # Track group if in a group
    if message.chat and message.chat.type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        await track_group(message.chat.id, message.chat.title)
        await init_group_auto_delete_settings(message.chat.id)

    # Add user to database for stats
    await add_user(user.id, user.first_name or "", user.username or "")

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "➕ Add to Group ➕",
                    url=f"https://t.me/{BOT_USERNAME}?startgroup=true",
                )
            ],
            [
                InlineKeyboardButton("Help ❓", callback_data="help"),
            ]
        ]
    )

    text = (
        "╔═══════════════════════════╗\n"
        "║  🤖 AFK ADVANCE BOT 🤖   ║\n"
        "║ Smart Away Status Manager ║\n"
        "╚═══════════════════════════╝\n\n"
        f"👋 **Welcome {user.first_name}!**\n\n"
        "I'm your personal AFK (Away From Keyboard) status manager. \n"
        "I'll notify everyone when you're away and automatically \n"
        "remove your AFK status when you return!\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Let's get started! 🚀"
    )

    await message.reply_text(
        text,
        reply_markup=keyboard,
        disable_web_page_preview=True
    )

# Help callback handler
@app.on_callback_query(filters.regex("^help$"))
async def help_callback(_, query: CallbackQuery):
    await query.answer()
    help_text = (
        "**📋 ALL COMMANDS**\n\n"
        "**To set AFK:**\n"
        "- /afk - Sets status with default message: \"Away from keyboard\"\n"
        "- Set media AFK - Reply to a photo, GIF, or sticker with /afk or brb. "
        "Your media will be shown to people who mention you\n\n"
        "**🔔 WHAT HAPPENS WHEN YOU'RE AFK?**\n"
        "✅ Someone mentions you (@username). They see your AFK reason & duration!\n"
        "- Send any message to disable AFK\n\n"
        "**Other Commands:**\n"
        "- /stats - View detailed bot insights & activity statistics\n"
        "- /topafk - Display the Top users with the highest AFK duration"
    )

    try:
        await query.message.edit_text(
            help_text,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("◀️ Back", callback_data="back_to_start")]]
            ),
            disable_web_page_preview=True,
        )
    except Exception:
        # fallback to answer
        await query.answer("Help shown", show_alert=True)

# Back to start callback handler
@app.on_callback_query(filters.regex("^back_to_start$"))
async def back_callback(_, query: CallbackQuery):
    await query.answer()
    user = query.from_user
    if not user:
        return

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "➕ Add to Group ➕",
                    url=f"https://t.me/{BOT_USERNAME}?startgroup=true",
                )
            ],
            [
                InlineKeyboardButton("Help ❔", callback_data="help"),
            ]
        ]
    )

    text = (
        "╔═════════════════════════════╗\n"
        "║  🤖 AFK ADVANCE BOT 🤖     ║\n"
        "║   Smart Away Status Manager ║\n"
        "╚═════════════════════════════╝\n\n"
        f"👋 **Welcome {user.first_name}!**\n\n"
        "I'm your personal AFK (Away From Keyboard) status manager. \n"
        "I'll notify everyone when you're away and automatically \n"
        "remove your AFK status when you return!\n\n"
        "━━━━━━━━━━━━━  ━━━━━━━━━━━━━━━\n\n"
        "Let's get started! 🚀"
    )

    try:
        await query.message.edit_text(
            text,
            reply_markup=keyboard,
            disable_web_page_preview=True
        )
    except Exception:
        await query.message.reply_text(
            text,
            reply_markup=keyboard,
            disable_web_page_preview=True
         )
        
# AFK handler
@app.on_message(filters.command(["afk"], prefixes=["/", "!"]) | filters.regex(r"^brb\b", re.IGNORECASE))
async def afk_handler(_, message: Message):
    if not message:
        return
    if getattr(message, "sender_chat", None):
        return

    user = message.from_user
    if not user:
        return

    user_id = user.id

    verifier, reasondb = await is_afk(user_id)

    # Track group if in a group
    if message.chat and message.chat.type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        await track_group(message.chat.id, message.chat.title)
        await init_group_auto_delete_settings(message.chat.id)

    # Add user to database for stats (update name and username)
    await add_user(user_id, user.first_name or "", user.username or "")

    # Extract command and reason from message
    reason_text = None
    if message.text and message.text.lower().startswith("brb"):
        parts = message.text.split(" ", 1)
        reason_text = parts[1] if len(parts) > 1 else None
    else:
        cmd = getattr(message, "command", None)
        if cmd and len(cmd) > 1:
            reason_text = " ".join(cmd[1:])

    # User is returning from AFK
    if verifier:
        afk_start = reasondb.get("time", time.time())
        try:
            afk_duration = int(time.time() - float(afk_start))
        except Exception:
            afk_duration = 0
        await update_user_afk_time(user_id, afk_duration)
        await remove_afk(user_id)

        try:
            afktype = reasondb.get("type", "text")
            timeafk = reasondb.get("time", afk_start)
            data = reasondb.get("data", None)
            reasonafk = reasondb.get("reason", None)
            seenago = get_readable_time(int(time.time() - float(timeafk))) if timeafk else "some time"

            base_text = f"🌟 **Welcome Back!**\n\n**{user.first_name}** has returned after being AFK for {seenago}"
            if reasonafk:
                base_text += f"\n\n📝 **AFK Reason:** `{reasonafk}`"
            base_text += "\n\n✅ Status: **Online**"

            # Prefer sending stored file_id if available. If photo type used local file, fallback to file path.
            if afktype == "animation" and data:
                sent_msg = await message.reply_animation(data, caption=base_text)
            elif afktype == "photo":
                # if data exists (file_id), use it; else use local file download path
                if data:
                    sent_msg = await message.reply_photo(photo=data, caption=base_text)
                else:
                    local_path = f"downloads/{user_id}.jpg"
                    if os.path.exists(local_path):
                        sent_msg = await message.reply_photo(photo=local_path, caption=base_text)
                    else:
                        sent_msg = await message.reply_text(base_text)
            else:
                sent_msg = await message.reply_text(base_text, disable_web_page_preview=True)
            await track_message_for_deletion(sent_msg)
        except Exception as e:
            logger.error(f"Error in AFK return: {e}")
            sent_msg = await message.reply_text(f"**{user.first_name}** is active again", disable_web_page_preview=True)
            await track_message_for_deletion(sent_msg)
        return

    # Setting new AFK status
    details = {
        "type": "text",
        "time": time.time(),
        "data": None,
        "reason": (reason_text[:100] if reason_text else None),
    }

    # Handle media in the same message (prefer file_id storage)
    try:
        if message.animation:
            details.update({"type": "animation", "data": message.animation.file_id, "time": time.time()})
        elif message.photo:
            # store file_id of the largest available photo
            try:
                if isinstance(message.photo, (list, tuple)):
                    file_id = message.photo[-1].file_id
                else:
                    file_id = message.photo.file_id
                details.update({"type": "photo", "data": file_id, "time": time.time()})
            except Exception:
                details.update({"type": "photo", "data": None, "time": time.time()})
        # handle replies to media
        elif message.reply_to_message:
            rm = message.reply_to_message
            if rm.animation:
                details.update({"type": "animation", "data": rm.animation.file_id, "time": time.time()})
            elif rm.photo:
                try:
                    if isinstance(rm.photo, (list, tuple)):
                        file_id = rm.photo[-1].file_id
                    else:
                        file_id = rm.photo.file_id
                    details.update({"type": "photo", "data": file_id, "time": time.time()})
                except Exception:
                    details.update({"type": "photo", "data": None, "time": time.time()})
            elif rm.sticker:
                # store sticker file_id (works for static stickers)
                try:
                    details.update({"type": "sticker", "data": rm.sticker.file_id, "time": time.time()})
                except Exception:
                    details.update({"type": "text", "data": None, "time": time.time()})
    except Exception as e:
        logger.error(f"Error while extracting media for AFK: {e}")

    # Save AFK status to database
    await add_afk(user_id, details)
    response = f"**{user.first_name}** is now AFK"
    if details.get("reason"):
        response += f"\n\nReason: `{details['reason']}`"
    sent_msg = await message.reply_text(response)
    await track_message_for_deletion(sent_msg)

# AFK watcher
@app.on_message(
    filters.group & ~filters.bot & ~filters.me & ~filters.service,
    group=1
)
async def afk_watcher(_, message: Message):
    if not message or not message.from_user:
        return

    userid = message.from_user.id
    user_name = message.from_user.first_name or "User"

    # Track group
    if message.chat:
        await track_group(message.chat.id, message.chat.title)
        await init_group_auto_delete_settings(message.chat.id)

    # Add user to database for stats (update name and username)
    await add_user(userid, message.from_user.first_name or "", message.from_user.username or "")

    # Check if user is returning from AFK
    verifier, reasondb = await is_afk(userid)
    if verifier:
        # Skip when the message is actually an AFK command
        text_lower = ((message.text or "") + " " + (message.caption or "")).lower()
        if any(cmd in text_lower for cmd in ["/afk", "!afk", "brb"]):
            return

        afk_start = reasondb.get("time", time.time())
        try:
            afk_duration = int(time.time() - float(afk_start))
        except Exception:
            afk_duration = 0
        await update_user_afk_time(userid, afk_duration)
        await remove_afk(userid)

        try:
            afktype = reasondb.get("type", "text")
            timeafk = reasondb.get("time", afk_start)
            data = reasondb.get("data")
            reasonafk = reasondb.get("reason")
            seenago = get_readable_time(int(time.time() - float(timeafk))) if timeafk else "some time"

            base_text = f"**{user_name}** is now available again after {seenago}"
            if reasonafk:
                base_text += f"\n\nReason: `{reasonafk}`"

            if afktype == "animation" and data:
                sent_msg = await message.reply_animation(data, caption=base_text)
            elif afktype in ("photo", "sticker"):
                if data:
                    sent_msg = await message.reply_photo(photo=data, caption=base_text)
                else:
                    local_path = f"downloads/{userid}.jpg"
                    if os.path.exists(local_path):
                        sent_msg = await message.reply_photo(photo=local_path, caption=base_text)
                    else:
                        sent_msg = await message.reply_text(base_text)
            else:
                sent_msg = await message.reply_text(base_text, disable_web_page_preview=True)
            await track_message_for_deletion(sent_msg)
        except Exception as e:
            logger.error(f"Error in AFK return watcher: {e}")
            sent_msg = await message.reply_text(f"**{user_name}** is active again")
            await track_message_for_deletion(sent_msg)

    # Check if replying to AFK user
    if message.reply_to_message and message.reply_to_message.from_user:
        try:
            replied_user = message.reply_to_message.from_user
            verifier, reasondb = await is_afk(replied_user.id)

            if verifier:
                afktype = reasondb.get("type", "text")
                timeafk = reasondb.get("time", time.time())
                data = reasondb.get("data")
                reasonafk = reasondb.get("reason")
                seenago = get_readable_time(int(time.time() - float(timeafk))) if timeafk else "some time"

                base_text = f"**{replied_user.first_name}** is AFK since {seenago}"
                if reasonafk:
                    base_text += f"\n\nReason: `{reasonafk}`"

                if afktype == "animation" and data:
                    sent_msg = await message.reply_animation(data, caption=base_text)
                elif afktype in ("photo", "sticker"):
                    if data:
                        sent_msg = await message.reply_photo(photo=data, caption=base_text)
                    else:
                        local_path = f"downloads/{replied_user.id}.jpg"
                        if os.path.exists(local_path):
                            sent_msg = await message.reply_photo(photo=local_path, caption=base_text)
                        else:
                            sent_msg = await message.reply_text(base_text)
                else:
                    sent_msg = await message.reply_text(base_text)
                await track_message_for_deletion(sent_msg)
        except Exception as e:
            logger.error(f"Error in AFK reply watcher: {e}")

    # Check mentioned users
    text_to_scan = message.text or ""
    if message.entities and text_to_scan:
        for entity in message.entities:
            try:
                if entity.type == enums.MessageEntityType.MENTION:
                    mentioned_text = text_to_scan[entity.offset:entity.offset + entity.length]
                    mentioned_username = mentioned_text.lstrip("@")
                    if mentioned_username.lower() == BOT_USERNAME.lower():
                        continue
                    try:
                        user_obj = await app.get_users(mentioned_username)
                    except Exception:
                        continue
                    if user_obj.id == message.from_user.id:
                        continue
                    verifier, reasondb = await is_afk(user_obj.id)
                    if verifier:
                        afktype = reasondb.get("type", "text")
                        timeafk = reasondb.get("time", time.time())
                        data = reasondb.get("data")
                        reasonafk = reasondb.get("reason")
                        seenago = get_readable_time(int(time.time() - float(timeafk))) if timeafk else "some time"

                        base_text = f"**{user_obj.first_name}** is AFK since {seenago}"
                        if reasonafk:
                            base_text += f"\n\nReason: `{reasonafk}`"

                        if afktype == "animation" and data:
                            sent_msg = await message.reply_animation(data, caption=base_text)
                        elif afktype in ("photo", "sticker"):
                            if data:
                                sent_msg = await message.reply_photo(photo=data, caption=base_text)
                            else:
                                local_path = f"downloads/{user_obj.id}.jpg"
                                if os.path.exists(local_path):
                                    sent_msg = await message.reply_photo(photo=local_path, caption=base_text)
                                else:
                                    sent_msg = await message.reply_text(base_text)
                        else:
                            sent_msg = await message.reply_text(base_text)
                        await track_message_for_deletion(sent_msg)

                elif entity.type == enums.MessageEntityType.TEXT_MENTION:
                    user_obj = entity.user
                    if not user_obj or user_obj.id == message.from_user.id:
                        continue
                    verifier, reasondb = await is_afk(user_obj.id)
                    if verifier:
                        afktype = reasondb.get("type", "text")
                        timeafk = reasondb.get("time", time.time())
                        data = reasondb.get("data")
                        reasonafk = reasondb.get("reason")
                        seenago = get_readable_time(int(time.time() - float(timeafk))) if timeafk else "some time"

                        base_text = f"**{user_obj.first_name}** is AFK since {seenago}"
                        if reasonafk:
                            base_text += f"\n\nReason: `{reasonafk}`"

                        if afktype == "animation" and data:
                            sent_msg = await message.reply_animation(data, caption=base_text)
                        elif afktype in ("photo", "sticker"):
                            if data:
                                sent_msg = await message.reply_photo(photo=data, caption=base_text)
                            else:
                                local_path = f"downloads/{user_obj.id}.jpg"
                                if os.path.exists(local_path):
                                    sent_msg = await message.reply_photo(photo=local_path, caption=base_text)
                                else:
                                    sent_msg = await message.reply_text(base_text)
                        else:
                            sent_msg = await message.reply_text(base_text)
                        await track_message_for_deletion(sent_msg)
            except Exception as e:
                logger.error(f"Error handling mention: {e}")

# Helper function for user broadcasting
async def broadcast_to_users(message, broadcast_type, text=None, replied_msg=None):
    total = 0
    success = 0
    failed = 0

    users = await users_collection.distinct("user_id")
    total_users = len(users)

    status = await message.reply_text(f"📤 Broadcasting to {total_users} users...")

    for user_id in users:
        try:
            if text:
                sent_msg = await app.send_message(chat_id=user_id, text=text)
                await track_message_for_deletion(sent_msg)
            elif replied_msg:
                if broadcast_type == "bcast":
                    sent_msg = await app.copy_message(
                        chat_id=user_id,
                        from_chat_id=replied_msg.chat.id,
                        message_id=replied_msg.id
                    )
                else:  # fcast
                    sent_msg = await app.forward_messages(
                        chat_id=user_id,
                        from_chat_id=replied_msg.chat.id,
                        message_ids=replied_msg.id
                    )
                await track_message_for_deletion(sent_msg)
            success += 1
        except Exception as e:
            failed += 1
            logger.error(f"Failed to send to {user_id}: {e}")

        total += 1
        if total % 100 == 0:
            try:
                await status.edit_text(f"👤 User broadcast: {total}/{total_users}")
            except Exception:
                pass

    return total_users, success, failed, status

# Helper function for group broadcasting
async def broadcast_to_groups(message, broadcast_type, text=None, replied_msg=None, exclude_chat_id=None, pin_message=False):
    total = 0
    success = 0
    failed = 0

    groups = await get_all_groups()
    total_groups = len(groups)

    status = await message.reply_text(f"📤 Broadcasting to {total_groups} groups...")

    for group in groups:
        try:
            group_chat_id = group.get("chat_id")
            if not group_chat_id:
                continue
            # Skip excluded chat
            if exclude_chat_id and group_chat_id == exclude_chat_id:
                continue

            sent_msg = None
            if text:
                sent_msg = await app.send_message(chat_id=group_chat_id, text=text)
            elif replied_msg:
                if broadcast_type == "bcast":
                    sent_msg = await app.copy_message(
                        chat_id=group_chat_id,
                        from_chat_id=replied_msg.chat.id,
                        message_id=replied_msg.id
                    )
                else:
                    sent_msg = await app.forward_messages(
                        chat_id=group_chat_id,
                        from_chat_id=replied_msg.chat.id,
                        message_ids=replied_msg.id
                    )

            if pin_message and sent_msg:
                try:
                    await app.pin_chat_message(chat_id=group_chat_id, message_id=sent_msg.id)
                except ChatAdminRequired:
                    logger.warning(f"Bot lacks permission to pin in group {group_chat_id}")
                except Exception as e:
                    logger.error(f"Pin message failed in group {group_chat_id}: {e}")

            if sent_msg:
                await track_message_for_deletion(sent_msg)

            success += 1
        except Exception as e:
            failed += 1
            logger.error(f"Failed to send to group {group.get('chat_id')}: {e}")

        total += 1
        if total % 10 == 0:
            try:
                await status.edit_text(f"👥 Group broadcast: {total}/{total_groups}")
            except Exception:
                pass

    return total_groups, success, failed, status

# Broadcast command with inline options
@app.on_message(filters.command(["bcast", "fcast"]) & filters.user(OWNER_ID))
async def broadcast_menu(_, message: Message):
    broadcast_id = generate_random_id()

    if message.chat and message.chat.type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        await track_group(message.chat.id, message.chat.title)

    text_content = None
    replied_msg = None

    if message.reply_to_message:
        replied_msg = message.reply_to_message
    elif message.text and getattr(message, "command", None) and len(message.command) > 1:
        text_content = " ".join(message.command[1:])

    await broadcast_collection.update_one(
        {"broadcast_id": broadcast_id},
        {"$set": {
            "command": message.command[0].lower() if getattr(message, "command", None) else "bcast",
            "text": text_content,
            "replied_msg_id": replied_msg.id if replied_msg else None,
            "replied_chat_id": replied_msg.chat.id if replied_msg else None,
            "original_chat_id": message.chat.id if message.chat else None,
            "original_msg_id": message.id,
            "timestamp": datetime.utcnow()
        }},
        upsert=True
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📍 Pin", callback_data=f"broadcast_option:{broadcast_id}:pin"),
            InlineKeyboardButton("👥 Group", callback_data=f"broadcast_option:{broadcast_id}:group")
        ],
        [
            InlineKeyboardButton("👤 User", callback_data=f"broadcast_option:{broadcast_id}:user")
        ],
        [
            InlineKeyboardButton("🚀 Send Now", callback_data=f"broadcast_confirm:{broadcast_id}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"broadcast_cancel:{broadcast_id}")
        ]
    ])

    text = "🔔 **Broadcast Options**\n\n"
    if text_content:
        text += f"Message: {text_content[:100]}{'...' if len(text_content) > 100 else ''}\n\n"
    elif replied_msg:
        text += "Message: Replied content\n\n"
    else:
        text += "⚠️ No message content provided\n\n"

    text += "Select options:"

    sent_msg = await message.reply_text(text, reply_markup=keyboard)
    await track_message_for_deletion(sent_msg)

# Callback handler for broadcast options
@app.on_callback_query(filters.regex(r"^broadcast_option:(\w+):(\w+)$"))
async def broadcast_option_handler(_, query: CallbackQuery):
    await query.answer()
    parts = query.data.split(":")
    if len(parts) < 3:
        await query.message.edit_text("❌ Invalid broadcast option")
        return
    broadcast_id = parts[1]
    option = parts[2]

    broadcast_data = await broadcast_collection.find_one({"broadcast_id": broadcast_id})
    if not broadcast_data:
        await query.message.edit_text("❌ Broadcast session expired or invalid")
        return

    current_options = broadcast_data.get("options", [])
    if option in current_options:
        current_options.remove(option)
    else:
        current_options.append(option)

    await broadcast_collection.update_one(
        {"broadcast_id": broadcast_id},
        {"$set": {"options": current_options}}
    )

    text = "🔔 **Broadcast Options**\n\n"
    if broadcast_data.get("text"):
        text += f"Message: {broadcast_data['text'][:100]}{'...' if len(broadcast_data['text']) > 100 else ''}\n\n"
    elif broadcast_data.get("replied_msg_id"):
        text += "Message: Replied content\n\n"
    else:
        text += "⚠️ No message content provided\n\n"

    text += "**Selected Options:**\n"
    text += f"- 📍 Pin: {'✅' if 'pin' in current_options else '❌'}\n"
    text += f"- 👥 Group: {'✅' if 'group' in current_options else '❌'}\n"
    text += f"- 👤 User: {'✅' if 'user' in current_options else '❌'}\n\n"
    text += "Select options:"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📍 Pin", callback_data=f"broadcast_option:{broadcast_id}:pin"),
            InlineKeyboardButton("👥 Group", callback_data=f"broadcast_option:{broadcast_id}:group")
        ],
        [
            InlineKeyboardButton("👤 User", callback_data=f"broadcast_option:{broadcast_id}:user")
        ],
        [
            InlineKeyboardButton("🚀 Send Now", callback_data=f"broadcast_confirm:{broadcast_id}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"broadcast_cancel:{broadcast_id}")
        ]
    ])

    try:
        await query.message.edit_text(text, reply_markup=keyboard)
    except Exception:
        await query.answer("Updated options", show_alert=True)

# Callback handler for broadcast confirmation
@app.on_callback_query(filters.regex(r"^broadcast_confirm:(\w+)$"))
async def broadcast_confirm_handler(_, query: CallbackQuery):
    await query.answer()
    broadcast_id = query.data.split(":")[1]

    broadcast_data = await broadcast_collection.find_one({"broadcast_id": broadcast_id})
    if not broadcast_data:
        await query.message.edit_text("❌ Broadcast session expired or invalid")
        return

    options = broadcast_data.get("options", [])
    command = broadcast_data.get("command", "bcast")
    chat_id = broadcast_data.get("original_chat_id")

    current_msg = None
    replied_msg = None
    try:
        if broadcast_data.get("text"):
            current_msg = await app.send_message(chat_id=chat_id, text=broadcast_data["text"])
            await track_message_for_deletion(current_msg)
        elif broadcast_data.get("replied_msg_id"):
            replied_msg = await app.get_messages(broadcast_data["replied_chat_id"], broadcast_data["replied_msg_id"])
            if command == "bcast":
                current_msg = await app.copy_message(chat_id=chat_id, from_chat_id=replied_msg.chat.id, message_id=replied_msg.id)
            else:
                current_msg = await app.forward_messages(chat_id=chat_id, from_chat_id=replied_msg.chat.id, message_ids=replied_msg.id)
            await track_message_for_deletion(current_msg)
    except Exception as e:
        logger.error(f"Current chat broadcast failed: {e}")
        try:
            await query.message.edit_text(f"❌ Failed to send in current chat: {e}")
        except Exception:
            pass

    group_stats = ""
    group_success = False
    if "group" in options:
        try:
            if broadcast_data.get("text"):
                total_groups, success, failed, status = await broadcast_to_groups(query.message, command, text=broadcast_data["text"], exclude_chat_id=chat_id, pin_message=("pin" in options))
            else:
                total_groups, success, failed, status = await broadcast_to_groups(query.message, command, replied_msg=replied_msg, exclude_chat_id=chat_id, pin_message=("pin" in options))

            group_stats = (
                f"\n👥 **Group Broadcast Stats**\n"
                f"• Total groups: {total_groups}\n"
                f"• Successful: {success}\n"
                f"• Failed: {failed}"
            )
            group_success = True
        except Exception as e:
            logger.error(f"Group broadcast failed: {e}")
            group_stats = f"\n❌ Group broadcast failed: {e}"

    user_stats = ""
    user_success = False
    if "user" in options:
        try:
            if broadcast_data.get("text"):
                total_users, success, failed, status = await broadcast_to_users(query.message, command, text=broadcast_data["text"])
            else:
                total_users, success, failed, status = await broadcast_to_users(query.message, command, replied_msg=replied_msg)

            user_stats = (
                f"\n👤 **User Broadcast Stats**\n"
                f"• Total users: {total_users}\n"
                f"• Successful: {success}\n"
                f"• Failed: {failed}"
            )
            user_success = True
        except Exception as e:
            logger.error(f"User broadcast failed: {e}")
            user_stats = f"\n❌ User broadcast failed: {e}"

    result_text = "✅ **Broadcast Completed**\n\n"
    if current_msg:
        result_text += f"📍 Current chat message: Sent\n"
    result_text += f"👥 Group broadcast: {'Sent' if group_success else 'Skipped'}\n"
    result_text += f"👤 User broadcast: {'Sent' if user_success else 'Skipped'}"
    result_text += group_stats
    result_text += user_stats

    keyboard = None
    try:
        if current_msg and chat_id:
            if str(chat_id).startswith("-100"):
                chat_id_str = str(chat_id).replace('-100', '')
                keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔍 View in Group", url=f"https://t.me/c/{chat_id_str}/{current_msg.id}")]])
            else:
                keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔍 View Message", url=f"https://t.me/c/{chat_id}/{current_msg.id}")]])
    except Exception:
        keyboard = None

    try:
        await query.message.edit_text(result_text, reply_markup=keyboard)
    except Exception:
        await query.answer("Broadcast completed", show_alert=True)

    await broadcast_collection.delete_one({"broadcast_id": broadcast_id})

# Callback handler for broadcast cancellation
@app.on_callback_query(filters.regex(r"^broadcast_cancel:(\w+)$"))
async def broadcast_cancel_handler(_, query: CallbackQuery):
    await query.answer("Broadcast cancelled")
    broadcast_id = query.data.split(":")[1]
    await broadcast_collection.delete_one({"broadcast_id": broadcast_id})
    try:
        await query.message.edit_text("❌ Broadcast cancelled")
    except Exception:
        pass

# Stats command
@app.on_message(filters.command("stats"))
async def stats_command(_, message: Message):
    uptime = get_readable_time(int(time.time() - BOT_START_TIME))
    total_users = await users_collection.count_documents({})
    afk_users = await afk_collection.count_documents({})
    total_groups = await groups_collection.count_documents({})

    stats_text = (
        f"🤖 **Bot Statistics**\n"
        f"• Uptime: `{uptime}`\n"
        f"• Total Users: `{total_users}`\n"
        f"• AFK Users: `{afk_users}`\n"
        f"• Groups Added: `{total_groups}`"
    )

    sent_msg = await message.reply_text(stats_text)
    await track_message_for_deletion(sent_msg)

# Top AFK command
@app.on_message(filters.command("topafk"))
async def top_afk_command(_, message: Message):
    top_users = await get_top_afk_users(10)

    if not top_users:
        await message.reply_text("No AFK time recorded yet.")
        return

    text = "🏆 **Top 10 AFK Users**\n\n"
    for idx, user in enumerate(top_users, start=1):
        user_id = user.get("user_id")
        total_time = user.get("total_afk_time", 0)
        first_name = user.get("first_name", "Unknown")
        username = user.get("username", "")

        if username:
            name_display = f"@{username}"
        else:
            name_display = first_name

        time_str = get_readable_time(total_time)
        text += f"{idx}. **{name_display}** – {time_str}\n"

    sent_msg = await message.reply_text(text)
    await track_message_for_deletion(sent_msg)

# Auto-delete menu command (inline buttons) - Per Group Settings
@app.on_message(filters.command(["autodel", "autodelete"]) & filters.group)
async def auto_delete_menu(_, message: Message):
    chat_id = message.chat.id
    await init_group_auto_delete_settings(chat_id)

    try:
        member = await app.get_chat_member(chat_id, message.from_user.id)
        if member.status not in [enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]:
            await message.reply_text("Administrator access is required to manage auto-delete settings 🔒")
            return
    except Exception as e:
        logger.error(f"Admin check error: {e}")
        await message.reply_text("❌ Failed to verify admin status")
        return

    text, keyboard = await get_auto_delete_menu(chat_id)
    sent_msg = await message.reply_text(text, reply_markup=keyboard)
    await track_message_for_deletion(sent_msg)

# Auto-delete callback handler - FIXED VERSION
@app.on_callback_query(filters.regex(r"^autodel_"))
async def auto_delete_callback(_, query: CallbackQuery):
    """Handle auto-delete callback actions with group-specific settings"""
    try:
        data = query.data

        # parse data
        if data.startswith("autodel_time:"):
            parts = data.split(':')
            if len(parts) < 3:
                await query.answer("Invalid data", show_alert=True)
                return
            seconds = int(parts[1])
            chat_id = int(parts[2])
            action = "time"
        else:
            # e.g. autodel_enable:CHAT_ID or autodel_disable:CHAT_ID etc
            parts = data.split(':')
            action = parts[0].replace("autodel_", "")
            chat_id = int(parts[1]) if len(parts) > 1 else None

        # permission check
        try:
            member = await app.get_chat_member(chat_id, query.from_user.id)
            if member.status not in [enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]:
                await query.answer("Administrator access is required to use this", show_alert=True)
                return
        except Exception as e:
            logger.error(f"Admin check error: {e}")
            await query.answer("❌ Permission check failed", show_alert=True)
            return

        await query.answer()

        if action == "enable":
            await toggle_auto_delete(chat_id, True)
            current_time = await get_auto_delete_time(chat_id)
            minutes = current_time // 60
            text = (
                "✅ Auto-delete has been enabled for this group\n\n"
                f"• Current delete time: `{minutes} minutes`\n\n"
                "Use the buttons below to manage settings:"
            )
            await query.message.edit_text(
                text,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back to Menu", callback_data=f"autodel_back:{chat_id}")],
                    [InlineKeyboardButton("❌ Close", callback_data=f"autodel_close:{chat_id}")]
                ])
            )

        elif action == "disable":
            await toggle_auto_delete(chat_id, False)
            await query.message.edit_text(
                "❌ Auto-delete has been disabled for this group\n\n"
                "Bot messages in this group will no longer be automatically deleted.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back to Menu", callback_data=f"autodel_back:{chat_id}")],
                    [InlineKeyboardButton("❌ Close", callback_data=f"autodel_close:{chat_id}")]
                ])
            )

        elif action == "time":
            minutes = seconds // 60
            await set_auto_delete_time(chat_id, seconds)
            await toggle_auto_delete(chat_id, True)
            await query.message.edit_text(
                f"✅ Auto-delete time set to {minutes} minutes and enabled for this group",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back to Menu", callback_data=f"autodel_back:{chat_id}")],
                    [InlineKeyboardButton("❌ Close", callback_data=f"autodel_close:{chat_id}")]
                ])
            )

        elif action == "close":
            try:
                await query.message.delete()
            except Exception:
                pass

        elif action == "back":
            text, keyboard = await get_auto_delete_menu(chat_id)
            await query.message.edit_text(text, reply_markup=keyboard)

    except Exception as e:
        logger.error(f"Error in auto-delete callback: {e}")
        try:
            await query.answer("An error occurred. Please try again.", show_alert=True)
        except Exception:
            pass

# Main execution
async def main():
    # Create downloads directory if not exists
    os.makedirs("downloads", exist_ok=True)
    logger.info("Created downloads directory")

    # Start auto-delete background task
    asyncio.create_task(auto_delete_loop())

    # Start Flask server in a separate thread
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info(f"Flask server started on port {PORT}")

    # Start the Telegram bot
    await app.start()
    logger.info("Telegram bot is now running...")

    # Keep the bot running
    await idle()

if __name__ == "__main__":
    try:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
    finally:
        try:
            loop.run_until_complete(app.stop())
        except Exception:
            pass
        logger.info("Bot stopped")
