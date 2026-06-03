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
users_collection = db.users
groups_collection = db.groups
broadcast_collection = db.broadcast_tmp
auto_delete_collection = db.auto_delete
afk_stats_collection = db.afk_stats
achievements_collection = db.achievements
global_king_collection = db.global_king
group_king_collection = db.group_king

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

# Achievement System
ACHIEVEMENTS = {
    "first_afk": {"emoji": "🎉", "name": "First AFK", "description": "Set AFK for the first time"},
    "hour_master": {"emoji": "⏰", "name": "Hour Master", "description": "1 hour straight AFK"},
    "day_master": {"emoji": "📅", "name": "Day Master", "description": "24 hours straight AFK"},
    "bronze": {"emoji": "🥉", "name": "Bronze Achiever", "description": "Reach 1 hour total AFK"},
    "silver": {"emoji": "🥈", "name": "Silver Achiever", "description": "Reach 10 hours total AFK"},
    "gold": {"emoji": "🥇", "name": "Gold Achiever", "description": "Reach 100 hours total AFK"},
    "platinum": {"emoji": "💎", "name": "Platinum Achiever", "description": "Reach 500 hours total AFK"},
    "legendary": {"emoji": "👑", "name": "Legendary", "description": "Reach 1000 hours total AFK"},
    "afk_master": {"emoji": "🔥", "name": "AFK Master", "description": "50 times AFK"},
    "king": {"emoji": "👸", "name": "AFK King", "description": "Highest AFK user overall"},
}

async def check_and_unlock_achievements(user_id: int):
    """Check and unlock achievements for user"""
    try:
        user_data = await users_collection.find_one({"user_id": user_id})
        afk_stats = await afk_stats_collection.find_one({"user_id": user_id})
        achievements = await achievements_collection.find_one({"user_id": user_id})
        
        if not achievements:
            achievements = {"user_id": user_id, "unlocked": []}
        
        unlocked = achievements.get("unlocked", [])
        total_afk_time = user_data.get("total_afk_time", 0) if user_data else 0
        total_afks = afk_stats.get("total_afks", 0) if afk_stats else 0
        
        # Check each achievement
        new_unlocked = []
        
        if "first_afk" not in unlocked and total_afks >= 1:
            new_unlocked.append("first_afk")
        
        if "hour_master" not in unlocked and afk_stats and afk_stats.get("highest_afk", 0) >= 3600:
            new_unlocked.append("hour_master")
        
        if "day_master" not in unlocked and afk_stats and afk_stats.get("highest_afk", 0) >= 86400:
            new_unlocked.append("day_master")
        
        if "bronze" not in unlocked and total_afk_time >= 3600:
            new_unlocked.append("bronze")
        
        if "silver" not in unlocked and total_afk_time >= 36000:
            new_unlocked.append("silver")
        
        if "gold" not in unlocked and total_afk_time >= 360000:
            new_unlocked.append("gold")
        
        if "platinum" not in unlocked and total_afk_time >= 1800000:
            new_unlocked.append("platinum")
        
        if "legendary" not in unlocked and total_afk_time >= 3600000:
            new_unlocked.append("legendary")
        
        if "afk_master" not in unlocked and total_afks >= 50:
            new_unlocked.append("afk_master")
        
        if new_unlocked:
            unlocked.extend(new_unlocked)
            await achievements_collection.update_one(
                {"user_id": user_id},
                {"$set": {"unlocked": unlocked}},
                upsert=True
            )
            return new_unlocked
        
        return []
    except Exception as e:
        logger.error(f"Error checking achievements: {e}")
        return []

async def get_user_achievements(user_id: int):
    """Get all achievements for a user"""
    try:
        achievements = await achievements_collection.find_one({"user_id": user_id})
        return achievements.get("unlocked", []) if achievements else []
    except Exception as e:
        logger.error(f"Error getting achievements: {e}")
        return []

# AFK King/Queen System
async def update_global_king(user_id: int, user_name: str):
    """Update global AFK King"""
    try:
        user_data = await users_collection.find_one({"user_id": user_id})
        total_afk = user_data.get("total_afk_time", 0) if user_data else 0
        
        king_data = await global_king_collection.find_one({"rank": 1})
        
        if not king_data or total_afk > king_data.get("total_afk_time", 0):
            await global_king_collection.update_one(
                {"rank": 1},
                {"$set": {
                    "user_id": user_id,
                    "name": user_name,
                    "total_afk_time": total_afk,
                    "updated_at": datetime.utcnow()
                }},
                upsert=True
            )
            logger.info(f"Global King updated: {user_name}")
    except Exception as e:
        logger.error(f"Error updating global king: {e}")

async def update_group_king(chat_id: int, user_id: int, user_name: str):
    """Update group AFK King"""
    try:
        user_data = await users_collection.find_one({"user_id": user_id})
        total_afk = user_data.get("total_afk_time", 0) if user_data else 0
        
        king_data = await group_king_collection.find_one({"chat_id": chat_id})
        
        if not king_data or total_afk > king_data.get("total_afk_time", 0):
            await group_king_collection.update_one(
                {"chat_id": chat_id},
                {"$set": {
                    "user_id": user_id,
                    "name": user_name,
                    "total_afk_time": total_afk,
                    "updated_at": datetime.utcnow()
                }},
                upsert=True
            )
            logger.info(f"Group King updated for {chat_id}: {user_name}")
    except Exception as e:
        logger.error(f"Error updating group king: {e}")

async def get_global_king():
    """Get global AFK King"""
    try:
        king = await global_king_collection.find_one({"rank": 1})
        return king if king else None
    except Exception as e:
        logger.error(f"Error getting global king: {e}")
        return None

async def get_group_king(chat_id: int):
    """Get group AFK King"""
    try:
        king = await group_king_collection.find_one({"chat_id": chat_id})
        return king if king else None
    except Exception as e:
        logger.error(f"Error getting group king: {e}")
        return None

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

async def store_afk_duration(user_id: int, afk_duration: int):
    """Store the AFK duration and update highest AFK if this is higher"""
    try:
        user_afk = await afk_stats_collection.find_one({"user_id": user_id})
        
        if user_afk:
            if afk_duration > user_afk.get("highest_afk", 0):
                await afk_stats_collection.update_one(
                    {"user_id": user_id},
                    {
                        "$set": {
                            "highest_afk": afk_duration,
                            "last_updated": datetime.utcnow()
                        },
                        "$inc": {"total_afks": 1}
                    }
                )
            else:
                await afk_stats_collection.update_one(
                    {"user_id": user_id},
                    {"$inc": {"total_afks": 1}}
                )
        else:
            await afk_stats_collection.insert_one({
                "user_id": user_id,
                "highest_afk": afk_duration,
                "total_afks": 1,
                "created_at": datetime.utcnow(),
                "last_updated": datetime.utcnow()
            })
        
        logger.info(f"Stored AFK duration {afk_duration}s for user {user_id}")
        return True
    except Exception as e:
        logger.error(f"Error storing AFK duration: {e}")
        return False

async def get_highest_afk_duration(user_id: int) -> int:
    """Get the highest AFK duration for a user"""
    try:
        user_afk = await afk_stats_collection.find_one({"user_id": user_id})
        if user_afk:
            return user_afk.get("highest_afk", 0)
        return 0
    except Exception as e:
        logger.error(f"Error getting highest AFK: {e}")
        return 0

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
            "delete_after": 300
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

# Start command handler
@app.on_message(filters.command(["start"]))
async def start_command(_, message: Message):
    user = message.from_user
    if not user:
        return

    uptime = get_readable_time(int(time.time() - BOT_START_TIME))

    if message.chat and message.chat.type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        await track_group(message.chat.id, message.chat.title)
        await init_group_auto_delete_settings(message.chat.id)

    await add_user(user.id, user.first_name or "", user.username or "")

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✨ Add to Group ✨",
                    url=f"https://t.me/{BOT_USERNAME}?startgroup=true",
                )
            ],
            [
                InlineKeyboardButton("Help ❔", callback_data="help"),
            ]
        ]
    )

    text = (
        "💤 **AFK ADVANCE BOT**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👋 Hello **{user.first_name}**!\n\n"
        "🔹 Smart AFK Management\n"
        "🔹 Auto AFK Removal\n"
        "🔹 AFK Duration Tracking\n"
        "🔹 Media AFK Support\n"
        "🔹 Achievements & Leaderboard\n\n"
        "Stay connected, even when you're away. 🚀"
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
        "- /afk - Sets status with default message\n"
        "- Set media AFK - Reply to a photo/GIF/sticker with /afk\n\n"
        "**🔔 AFK COMMANDS:**\n"
        "- /stats - View bot statistics\n"
        "- /topafk - Top 10 AFK users globally\n"
        "- /my_records - Your personal AFK records & achievements 🏅\n"
        "- /afk_king - Global AFK King 👑\n"
        "- /group_king - Group AFK King 🏆\n"
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
                    "✨ Add to Group ✨",
                    url=f"https://t.me/{BOT_USERNAME}?startgroup=true",
                )
            ],
            [
                InlineKeyboardButton("Help ❔", callback_data="help"),
            ]
        ]
    )

    text = (
        "💤 **AFK ADVANCE BOT**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👋 Hello **{user.first_name}**!\n\n"
        "🔹 Smart AFK Management\n"
        "🔹 Auto AFK Removal\n"
        "🔹 AFK Duration Tracking\n"
        "🔹 Media AFK Support\n"
        "🔹 Achievements & Leaderboard\n\n"
        "Stay connected, even when you're away. 🚀"
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

# ✅ NEW: Achievements Command
@app.on_message(filters.command("afk_achievements"))
async def afk_achievements_command(_, message: Message):
    """Show user's achievements"""
    user_id = message.from_user.id
    user_name = message.from_user.first_name or "User"
    
    unlocked_achievements = await get_user_achievements(user_id)
    
    if not unlocked_achievements:
        text = f"📭 **{user_name}**, you haven't unlocked any achievements yet!\n\nGo AFK to unlock them! 🚀"
        sent_msg = await message.reply_text(text)
        await track_message_for_deletion(sent_msg)
        return
    
    text = f"🏅 **{user_name}'s Achievements**\n\n"
    text += f"Total Unlocked: <code>{len(unlocked_achievements)}</code>\n\n"
    
    for achievement_key in unlocked_achievements:
        if achievement_key in ACHIEVEMENTS:
            ach = ACHIEVEMENTS[achievement_key]
            text += f"{ach['emoji']} <b>{ach['name']}</b>\n"
            text += f"   <i>{ach['description']}</i>\n\n"
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 My Records", callback_data=f"view_my_records_{user_id}")]
    ])
    
    sent_msg = await message.reply_text(text, reply_markup=keyboard, parse_mode=enums.ParseMode.HTML)
    await track_message_for_deletion(sent_msg)

# ✅ FIXED: View My Records callback
@app.on_callback_query(filters.regex(r"^view_my_records_"))
async def view_my_records_callback(_, query: CallbackQuery):
    """Show personal AFK records from achievements view"""
    await query.answer()
    try:
        user_id = int(query.data.split("_")[-1])
        user_name = query.from_user.first_name or "User"
        
        user_data = await users_collection.find_one({"user_id": user_id})
        afk_stats = await afk_stats_collection.find_one({"user_id": user_id})
        
        total_afk = user_data.get("total_afk_time", 0) if user_data else 0
        highest_afk = afk_stats.get("highest_afk", 0) if afk_stats else 0
        total_afks = afk_stats.get("total_afks", 0) if afk_stats else 0
        avg_afk = (total_afk // total_afks) if total_afks > 0 else 0
        
        text = (
            f"📊 <b>{user_name}'s AFK Records</b>\n"
            f"{'─' * 40}\n\n"
            f"⏱️  <b>Longest AFK:</b> <code>{get_readable_time(highest_afk)}</code>\n"
            f"⏳ <b>Total AFK Time:</b> <code>{get_readable_time(total_afk)}</code>\n"
            f"🔄 <b>AFK Count:</b> <code>{total_afks}</code>\n"
            f"📈 <b>Average AFK:</b> <code>{get_readable_time(avg_afk)}</code>\n"
        )
        
        unlocked = await get_user_achievements(user_id)
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🏅 Back to Achievements", callback_data=f"back_to_achievements_{user_id}")],
            [InlineKeyboardButton("❌ Close", callback_data="close_message")]
        ])
        
        await query.message.edit_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Error in view_my_records_callback: {e}")
        await query.answer("❌ Error loading records", show_alert=True)

# ✅ FIXED: Back to Achievements callback
@app.on_callback_query(filters.regex(r"^back_to_achievements_"))
async def back_to_achievements_callback(_, query: CallbackQuery):
    """Back to achievements view"""
    await query.answer()
    try:
        user_id = int(query.data.split("_")[-1])
        user_name = query.from_user.first_name or "User"
        
        unlocked_achievements = await get_user_achievements(user_id)
        
        text = f"🏅 <b>{user_name}'s Achievements</b>\n\n"
        text += f"Total Unlocked: <code>{len(unlocked_achievements)}</code>\n\n"
        
        for achievement_key in unlocked_achievements:
            if achievement_key in ACHIEVEMENTS:
                ach = ACHIEVEMENTS[achievement_key]
                text += f"{ach['emoji']} <b>{ach['name']}</b>\n"
                text += f"   <i>{ach['description']}</i>\n\n"
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 My Records", callback_data=f"view_my_records_{user_id}")],
            [InlineKeyboardButton("❌ Close", callback_data="close_message")]
        ])
        
        await query.message.edit_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Error in back_to_achievements_callback: {e}")
        await query.answer("❌ Error", show_alert=True)

# ✅ Close message callback
@app.on_callback_query(filters.regex("^close_message$"))
async def close_message_callback(_, query: CallbackQuery):
    """Close the message"""
    await query.answer()
    try:
        await query.message.delete()
    except Exception:
        pass

# ✅ NEW: My Records Command (Direct)
@app.on_message(filters.command("my_records"))
async def my_records_command(_, message: Message):
    """Show personal AFK records"""
    user_id = message.from_user.id
    user_name = message.from_user.first_name or "User"
    
    user_data = await users_collection.find_one({"user_id": user_id})
    afk_stats = await afk_stats_collection.find_one({"user_id": user_id})
    
    total_afk = user_data.get("total_afk_time", 0) if user_data else 0
    highest_afk = afk_stats.get("highest_afk", 0) if afk_stats else 0
    total_afks = afk_stats.get("total_afks", 0) if afk_stats else 0
    avg_afk = (total_afk // total_afks) if total_afks > 0 else 0
    
    text = (
        f"✨ <b>{user_name}'s AFK Statistics</b> ✨\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🏆 <b>Longest AFK Session:</b>\n"
        f"   <code>{get_readable_time(highest_afk)}</code>\n\n"
        f"⏰ <b>Total AFK Time:</b>\n"
        f"   <code>{get_readable_time(total_afk)}</code>\n\n"
        f"🔄 <b>AFK Sessions:</b>\n"
        f"   <code>{total_afks}</code> times\n\n"
        f"  <b>Average Duration:</b>\n"
        f"   <code>{get_readable_time(avg_afk)}</code>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💎 Keep tracking your AFK journey!"
    )
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏅 My Achievements", callback_data=f"show_achievements_{user_id}")],
        [InlineKeyboardButton("❌ Close", callback_data="close_message")]
    ])
    
    sent_msg = await message.reply_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=keyboard)
    await track_message_for_deletion(sent_msg)

# ✅ Show achievements from my_records
@app.on_callback_query(filters.regex(r"^show_achievements_"))
async def show_achievements_callback(_, query: CallbackQuery):
    """Show achievements from my_records"""
    await query.answer()
    try:
        user_id = int(query.data.split("_")[-1])
        user_name = query.from_user.first_name or "User"
        
        unlocked_achievements = await get_user_achievements(user_id)
        
        if not unlocked_achievements:
            text = f"📭 <b>{user_name}</b>, you haven't unlocked any achievements yet!"
        else:
            text = f"🏅 <b>{user_name}'s Achievements</b>\n\n"
            text += f"Total Unlocked: <code>{len(unlocked_achievements)}</code>\n\n"
            
            for achievement_key in unlocked_achievements:
                if achievement_key in ACHIEVEMENTS:
                    ach = ACHIEVEMENTS[achievement_key]
                    text += f"{ach['emoji']} <b>{ach['name']}</b>\n"
                    text += f"   <i>{ach['description']}</i>\n\n"
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 Back to Records", callback_data=f"back_to_records_{user_id}")],
            [InlineKeyboardButton("❌ Close", callback_data="close_message")]
        ])
        
        await query.message.edit_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Error in show_achievements_callback: {e}")
        await query.answer("❌ Error", show_alert=True)

# ✅ Back to Records from achievements
@app.on_callback_query(filters.regex(r"^back_to_records_"))
async def back_to_records_callback(_, query: CallbackQuery):
    """Back to records from achievements"""
    await query.answer()
    try:
        user_id = int(query.data.split("_")[-1])
        user_name = query.from_user.first_name or "User"
        
        user_data = await users_collection.find_one({"user_id": user_id})
        afk_stats = await afk_stats_collection.find_one({"user_id": user_id})
        
        total_afk = user_data.get("total_afk_time", 0) if user_data else 0
        highest_afk = afk_stats.get("highest_afk", 0) if afk_stats else 0
        total_afks = afk_stats.get("total_afks", 0) if afk_stats else 0
        avg_afk = (total_afk // total_afks) if total_afks > 0 else 0
        
        text = (
            f"📊 <b>{user_name}'s AFK Records</b>\n"
            f"{'─' * 40}\n\n"
            f"⏱️  <b>Longest AFK:</b> <code>{get_readable_time(highest_afk)}</code>\n"
            f"⏳ <b>Total AFK Time:</b> <code>{get_readable_time(total_afk)}</code>\n"
            f"🔄 <b>AFK Count:</b> <code>{total_afks}</code>\n"
            f"📈 <b>Average AFK:</b> <code>{get_readable_time(avg_afk)}</code>\n"
        )
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🏅 My Achievements", callback_data=f"show_achievements_{user_id}")],
            [InlineKeyboardButton("❌ Close", callback_data="close_message")]
        ])
        
        await query.message.edit_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Error in back_to_records_callback: {e}")
        await query.answer("❌ Error", show_alert=True)

# ✅ NEW: Global AFK King Command
@app.on_message(filters.command("afk_king"))
async def afk_king_command(_, message: Message):
    """Show global AFK King"""
    king = await get_global_king()
    
    if not king:
        text = "👑 <b>Global AFK King</b>\n\nNo King crowned yet! Be the first! 🚀"
    else:
        total_time = king.get("total_afk_time", 0)
        readable_time = get_readable_time(total_time)
        text = (
            f"👑 <b>Global AFK King</b>\n\n"
            f"<b>Name:</b> {king.get('name', 'Unknown')}\n"
            f"<b>Total AFK:</b> <code>{readable_time}</code>\n"
            f"<b>User ID:</b> <code>{king.get('user_id')}</code>"
        )
    
    sent_msg = await message.reply_text(text, parse_mode=enums.ParseMode.HTML)
    await track_message_for_deletion(sent_msg)

# ✅ NEW: Group AFK King Command
@app.on_message(filters.command("group_king") & filters.group)
async def group_king_command(_, message: Message):
    """Show group AFK King"""
    chat_id = message.chat.id
    king = await get_group_king(chat_id)
    
    if not king:
        text = "🏆 <b>Group AFK King</b>\n\nNo King crowned yet in this group! Be the first! 🚀"
    else:
        total_time = king.get("total_afk_time", 0)
        readable_time = get_readable_time(total_time)
        text = (
            f"🏆 <b>Group AFK King</b>\n\n"
            f"<b>Name:</b> {king.get('name', 'Unknown')}\n"
            f"<b>Total AFK:</b> <code>{readable_time}</code>"
        )
    
    sent_msg = await message.reply_text(text, parse_mode=enums.ParseMode.HTML)
    await track_message_for_deletion(sent_msg)

# ✅ NEW: Leaderboard Command
@app.on_message(filters.command("leaderboard") & filters.group)
async def leaderboard_command(_, message: Message):
    """Show group leaderboard"""
    chat_id = message.chat.id
    
    top_users = await get_top_afk_users(10)
    
    if not top_users:
        text = "📈 <b>AFK Leaderboard</b>\n\nNo records yet!"
        sent_msg = await message.reply_text(text, parse_mode=enums.ParseMode.HTML)
        await track_message_for_deletion(sent_msg)
        return
    
    text = "📈 <b>Global AFK Leaderboard</b>\n\n"
    for idx, user in enumerate(top_users, start=1):
        first_name = user.get("first_name", "Unknown")
        total_time = user.get("total_afk_time", 0)
        readable_time = get_readable_time(total_time)
        
        if idx == 1:
            text += f"🥇 {idx}. <b>{first_name}</b> - {readable_time}\n"
        elif idx == 2:
            text += f"🥈 {idx}. <b>{first_name}</b> - {readable_time}\n"
        elif idx == 3:
            text += f"🥉 {idx}. <b>{first_name}</b> - {readable_time}\n"
        else:
            text += f"{idx}. <b>{first_name}</b> - {readable_time}\n"
    
    sent_msg = await message.reply_text(text, parse_mode=enums.ParseMode.HTML)
    await track_message_for_deletion(sent_msg)

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

    if message.chat and message.chat.type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        await track_group(message.chat.id, message.chat.title)
        await init_group_auto_delete_settings(message.chat.id)

    await add_user(user_id, user.first_name or "", user.username or "")

    reason_text = None
    if message.text and message.text.lower().startswith("brb"):
        parts = message.text.split(" ", 1)
        reason_text = parts[1] if len(parts) > 1 else None
    else:
        cmd = getattr(message, "command", None)
        if cmd and len(cmd) > 1:
            reason_text = " ".join(cmd[1:])

    if verifier:
        afk_start = reasondb.get("time", time.time())
        try:
            afk_duration = int(time.time() - float(afk_start))
        except Exception:
            afk_duration = 0
        
        await store_afk_duration(user_id, afk_duration)
        await update_user_afk_time(user_id, afk_duration)
        await remove_afk(user_id)
        
        # Update kings
        await update_global_king(user_id, user.first_name or "Unknown")
        if message.chat and message.chat.type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
            await update_group_king(message.chat.id, user_id, user.first_name or "Unknown")

        try:
            afktype = reasondb.get("type", "text")
            timeafk = reasondb.get("time", afk_start)
            data = reasondb.get("data", None)
            reasonafk = reasondb.get("reason", None)
            seenago = get_readable_time(int(time.time() - float(timeafk))) if timeafk else "some time"

            base_text = f"✨ **Welcome Back!** ✨\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n👤 **{user.first_name}** is now online again!\n\n⏱️ **Away Duration:** {seenago}\n"
            if reasonafk:
                base_text += f"📝 **Reason:** `{reasonafk}`\n"
            base_text += "━━━━━━━━━━━━━━━━━━━━━━━━━\n🟢 **Status:** Available"

            if afktype == "animation" and data:
                sent_msg = await message.reply_animation(data, caption=base_text)
            elif afktype == "photo":
                if data:
                    sent_msg = await message.reply_photo(photo=data, caption=base_text)
                else:
                    local_path = f"downloads/{user_id}.jpg"
                    if os.path.exists(local_path):
                        sent_msg = await message.reply_photo(photo=local_path, caption=base_text)
                    else:
                        sent_msg = await message.reply_text(base_text)
            elif afktype == "sticker":
                if data:
                    sent_msg = await message.reply_sticker(sticker=data)
                    await asyncio.sleep(0.5)
                    sent_msg = await message.reply_text(base_text)
                else:
                    sent_msg = await message.reply_text(base_text)
            else:
                sent_msg = await message.reply_text(base_text, disable_web_page_preview=True)
            await track_message_for_deletion(sent_msg)
        except Exception as e:
            logger.error(f"Error in AFK return: {e}")
            sent_msg = await message.reply_text(f"🌟 **Welcome Back!**\n\n**{user.first_name}** has returned after being AFK for {seenago}", disable_web_page_preview=True)
            await track_message_for_deletion(sent_msg)
        return

    details = {
        "type": "text",
        "time": time.time(),
        "data": None,
        "reason": (reason_text[:100] if reason_text else None),
    }

    try:
        if message.animation:
            details.update({"type": "animation", "data": message.animation.file_id, "time": time.time()})
        elif message.photo:
            try:
                if isinstance(message.photo, (list, tuple)):
                    file_id = message.photo[-1].file_id
                else:
                    file_id = message.photo.file_id
                details.update({"type": "photo", "data": file_id, "time": time.time()})
            except Exception:
                details.update({"type": "photo", "data": None, "time": time.time()})
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
                try:
                    details.update({"type": "sticker", "data": rm.sticker.file_id, "time": time.time()})
                except Exception:
                    details.update({"type": "text", "data": None, "time": time.time()})
    except Exception as e:
        logger.error(f"Error while extracting media for AFK: {e}")

    await add_afk(user_id, details)
    response = f"✨ **AFK Mode Activated** ✨\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n👤 **{user.first_name}** is now away from keyboard\n"
    if details.get("reason"):
        response += f"📝 **Reason:** `{details['reason']}`\n"
    response += "━━━━━━━━━━━━━━━━━━━━━━━━━\n🔴 **Status:** Away"
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

    if message.chat:
        await track_group(message.chat.id, message.chat.title)
        await init_group_auto_delete_settings(message.chat.id)

    await add_user(userid, message.from_user.first_name or "", message.from_user.username or "")

    verifier, reasondb = await is_afk(userid)
    if verifier:
        text_lower = ((message.text or "") + " " + (message.caption or "")).lower()
        if any(cmd in text_lower for cmd in ["/afk", "!afk", "brb"]):
            return

        afk_start = reasondb.get("time", time.time())
        try:
            afk_duration = int(time.time() - float(afk_start))
        except Exception:
            afk_duration = 0
        
        await store_afk_duration(userid, afk_duration)
        await update_user_afk_time(userid, afk_duration)
        await remove_afk(userid)
        
        # Update kings
        await update_global_king(userid, user_name)
        await update_group_king(message.chat.id, userid, user_name)

        try:
            afktype = reasondb.get("type", "text")
            timeafk = reasondb.get("time", afk_start)
            data = reasondb.get("data")
            reasonafk = reasondb.get("reason")
            seenago = get_readable_time(int(time.time() - float(timeafk))) if timeafk else "some time"

            base_text = f"✨ **Welcome Back!** ✨\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n👤 **{user_name}** is now online again!\n\n⏱️ **Away Duration:** {seenago}\n"
            if reasonafk:
                base_text += f"📝 **Reason:** `{reasonafk}`\n"
            base_text += "━━━━━━━━━━━━━━━━━━━━━━━━━\n🟢 **Status:** Available"

            if afktype == "animation" and data:
                sent_msg = await message.reply_animation(data, caption=base_text)
            elif afktype == "photo":
                if data:
                    sent_msg = await message.reply_photo(photo=data, caption=base_text)
                else:
                    local_path = f"downloads/{userid}.jpg"
                    if os.path.exists(local_path):
                        sent_msg = await message.reply_photo(photo=local_path, caption=base_text)
                    else:
                        sent_msg = await message.reply_text(base_text)
            elif afktype == "sticker":
                if data:
                    sent_msg = await message.reply_sticker(sticker=data)
                    await asyncio.sleep(0.5)
                    sent_msg = await message.reply_text(base_text)
                else:
                    sent_msg = await message.reply_text(base_text)
            else:
                sent_msg = await message.reply_text(base_text, disable_web_page_preview=True)
            await track_message_for_deletion(sent_msg)
        except Exception as e:
            logger.error(f"Error in AFK return watcher: {e}")
            sent_msg = await message.reply_text(f"**{user_name}** is now available again after some time")
            await track_message_for_deletion(sent_msg)

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

                base_text = f"💤 **{replied_user.first_name}** is currently away\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n⏱️ **Away for:** {seenago}\n"
                if reasonafk:
                    base_text += f"📝 **Reason:** `{reasonafk}`\n"
                base_text += "━━━━━━━━━━━━━━━━━━━━━━━━━\n🔴 **Status:** AFK"

                if afktype == "animation" and data:
                    sent_msg = await message.reply_animation(data, caption=base_text)
                elif afktype == "photo":
                    if data:
                        sent_msg = await message.reply_photo(photo=data, caption=base_text)
                    else:
                        local_path = f"downloads/{replied_user.id}.jpg"
                        if os.path.exists(local_path):
                            sent_msg = await message.reply_photo(photo=local_path, caption=base_text)
                        else:
                            sent_msg = await message.reply_text(base_text)
                elif afktype == "sticker":
                    if data:
                        sent_msg = await message.reply_sticker(sticker=data)
                        await asyncio.sleep(0.5)
                        await message.reply_text(base_text)
                    else:
                        sent_msg = await message.reply_text(base_text)
                else:
                    sent_msg = await message.reply_text(base_text)
                await track_message_for_deletion(sent_msg)
        except Exception as e:
            logger.error(f"Error in AFK reply watcher: {e}")

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

                        base_text = f"💤 **{user_obj.first_name}** is currently away\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n⏱️ **Away for:** {seenago}\n"
                        if reasonafk:
                            base_text += f"📝 **Reason:** `{reasonafk}`\n"
                        base_text += "━━━━━━━━━━━━━━━━━━━━━━━━━\n🔴 **Status:** AFK"

                        if afktype == "animation" and data:
                            sent_msg = await message.reply_animation(data, caption=base_text)
                        elif afktype == "photo":
                            if data:
                                sent_msg = await message.reply_photo(photo=data, caption=base_text)
                            else:
                                local_path = f"downloads/{user_obj.id}.jpg"
                                if os.path.exists(local_path):
                                    sent_msg = await message.reply_photo(photo=local_path, caption=base_text)
                                else:
                                    sent_msg = await message.reply_text(base_text)
                        elif afktype == "sticker":
                            if data:
                                sent_msg = await message.reply_sticker(sticker=data)
                                await asyncio.sleep(0.5)
                                await message.reply_text(base_text)
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

                        base_text = f"💤 **{user_obj.first_name}** is currently away\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n⏱️ **Away for:** {seenago}\n"
                        if reasonafk:
                            base_text += f"📝 **Reason:** `{reasonafk}`\n"
                        base_text += "━━━━━━━━━━━━━━━━━━━━━━━━━\n🔴 **Status:** AFK"

                        if afktype == "animation" and data:
                            sent_msg = await message.reply_animation(data, caption=base_text)
                        elif afktype == "photo":
                            if data:
                                sent_msg = await message.reply_photo(photo=data, caption=base_text)
                            else:
                                local_path = f"downloads/{user_obj.id}.jpg"
                                if os.path.exists(local_path):
                                    sent_msg = await message.reply_photo(photo=local_path, caption=base_text)
                                else:
                                    sent_msg = await message.reply_text(base_text)
                        elif afktype == "sticker":
                            if data:
                                sent_msg = await message.reply_sticker(sticker=data)
                                await asyncio.sleep(0.5)
                                await message.reply_text(base_text)
                            else:
                                sent_msg = await message.reply_text(base_text)
                        else:
                            sent_msg = await message.reply_text(base_text)
                        await track_message_for_deletion(sent_msg)
            except Exception as e:
                logger.error(f"Error handling mention: {e}")

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

# Broadcast command (Owner only)
@app.on_message(filters.command("broadcast"))
async def broadcast_command(_, message: Message):
    """Broadcast message to all groups and users (Owner only)"""
    if not OWNER_ID or message.from_user.id != OWNER_ID:
        await message.reply_text("❌ This command is only for the bot owner.")
        return

    # Check if it's a reply to a message
    if message.reply_to_message:
        broadcast_text = message.reply_to_message.text or message.reply_to_message.caption or ""
    else:
        # Get text from command
        parts = message.text.split(" ", 1)
        if len(parts) < 2:
            await message.reply_text("❌ Please provide a message to broadcast.\n\nUsage: /broadcast Your message here\nOr reply to a message with /broadcast")
            return
        broadcast_text = parts[1]

    if not broadcast_text:
        await message.reply_text("❌ No message to broadcast.")
        return

    # Get all groups and users
    groups = await get_all_groups()
    users_cursor = users_collection.find({})
    users = await users_cursor.to_list(length=None)

    total_sent = 0
    total_failed = 0
    invalid_groups = []
    invalid_users = []

    status_msg = await message.reply_text(f"📢 **Broadcast Started**\n\nSending to {len(groups)} groups and {len(users)} users...")

    # Send to groups
    for group in groups:
        try:
            await app.send_message(group["chat_id"], broadcast_text)
            total_sent += 1
            await asyncio.sleep(0.1)  # Small delay to avoid flood limits
        except Exception as e:
            logger.error(f"Failed to send to group {group['chat_id']}: {e}")
            total_failed += 1
            # Mark invalid groups for cleanup
            if "PeerIdInvalid" in str(e) or "PEER_ID_INVALID" in str(e):
                invalid_groups.append(group["chat_id"])

    # Send to users
    for user in users:
        try:
            await app.send_message(user["user_id"], broadcast_text)
            total_sent += 1
            await asyncio.sleep(0.1)  # Small delay to avoid flood limits
        except Exception as e:
            logger.error(f"Failed to send to user {user['user_id']}: {e}")
            total_failed += 1
            # Mark invalid users for cleanup
            if "PEER_ID_INVALID" in str(e) or "INPUT_USER_DEACTIVATED" in str(e):
                invalid_users.append(user["user_id"])

    # Clean up invalid groups from database
    if invalid_groups:
        for group_id in invalid_groups:
            await groups_collection.delete_one({"chat_id": group_id})
            logger.info(f"Removed invalid group {group_id} from database")

    # Clean up invalid users from database
    if invalid_users:
        for user_id in invalid_users:
            await users_collection.delete_one({"user_id": user_id})
            logger.info(f"Removed invalid user {user_id} from database")

    cleanup_msg = ""
    if invalid_groups or invalid_users:
        cleanup_msg = f"\n\n🧹 **Cleaned up:**\n"
        if invalid_groups:
            cleanup_msg += f"• {len(invalid_groups)} invalid groups\n"
        if invalid_users:
            cleanup_msg += f"• {len(invalid_users)} invalid users"

    await status_msg.edit_text(
        f"✅ **Broadcast Completed**\n\n"
        f"📊 **Statistics:**\n"
        f"• Total Sent: {total_sent}\n"
        f"• Total Failed: {total_failed}\n"
        f"• Groups: {len(groups)}\n"
        f"• Users: {len(users)}"
        f"{cleanup_msg}"
    )

# Auto-delete menu command
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

# Auto-delete callback handler
@app.on_callback_query(filters.regex(r"^autodel_"))
async def auto_delete_callback(_, query: CallbackQuery):
    """Handle auto-delete callback actions with group-specific settings"""
    try:
        data = query.data

        if data.startswith("autodel_time:"):
            parts = data.split(':')
            if len(parts) < 3:
                await query.answer("Invalid data", show_alert=True)
                return
            seconds = int(parts[1])
            chat_id = int(parts[2])
            action = "time"
        else:
            parts = data.split(':')
            action = parts[0].replace("autodel_", "")
            chat_id = int(parts[1]) if len(parts) > 1 else None

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
    os.makedirs("downloads", exist_ok=True)
    logger.info("Created downloads directory")

    asyncio.create_task(auto_delete_loop())

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info(f"Flask server started on port {PORT}")

    await app.start()
    logger.info("Telegram bot is now running...")

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
