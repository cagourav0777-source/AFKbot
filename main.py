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

# Import config
from config import (
    BOT_TOKEN, API_ID, API_HASH, BOT_USERNAME,
    MONGODB_URI, OWNER_ID, PORT
)

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Validate configuration on startup
logger.info("=" * 60)
logger.info("🚀 Starting AFK Bot...")
logger.info("=" * 60)

# Bot start time for uptime calculation
START_TIME = time.time()

# Initialize MongoDB with error handling
try:
    mongo_client = AsyncIOMotorClient(MONGODB_URI)
    db = mongo_client.afk_db
    afk_collection = db.afk
    users_collection = db.users
    groups_collection = db.groups
    broadcast_collection = db.broadcast_tmp
    auto_delete_collection = db.auto_delete
    logger.info("✅ MongoDB connection initialized")
except Exception as e:
    logger.error(f"❌ MongoDB connection failed: {e}")
    exit(1)

# ==================== HELPER FUNCTIONS ====================

def get_readable_time(seconds: int) -> str:
    """Convert seconds to readable format (days, hours, minutes, seconds)"""
    result = ''
    days, seconds = divmod(seconds, 86400)
    if days != 0:
        result += f'{days}d '
    hours, seconds = divmod(seconds, 3600)
    if hours != 0:
        result += f'{hours}h '
    minutes, seconds = divmod(seconds, 60)
    if minutes != 0:
        result += f'{minutes}m '
    seconds = int(seconds)
    result += f'{seconds}s'
    return result

def generate_random_id(length=8):
    """Generate random ID for broadcasts"""
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

# ==================== AFK FUNCTIONS ====================

async def add_afk(user_id: int, details: dict):
    """Add or update AFK status for user"""
    await afk_collection.update_one(
        {"user_id": user_id},
        {"$set": details},
        upsert=True
    )

async def is_afk(user_id: int):
    """Check if user is AFK"""
    data = await afk_collection.find_one({"user_id": user_id})
    if data:
        return True, data
    return False, {}

async def remove_afk(user_id: int):
    """Remove AFK status for user"""
    await afk_collection.delete_one({"user_id": user_id})

# ==================== USER FUNCTIONS ====================

async def add_user(user_id: int, first_name: str = "", username: str = ""):
    """Add or update user info with AFK time tracking"""
    await users_collection.update_one(
        {"user_id": user_id},
        {
            "$set": {
                "first_name": first_name,
                "username": username,
                "last_seen": datetime.now()
            },
            "$setOnInsert": {"total_afk_time": 0}
        },
        upsert=True
    )

async def count_users():
    """Count total users"""
    return await users_collection.count_documents({})

async def update_user_afk_time(user_id: int, additional_seconds: int):
    """Add to total AFK time for a user"""
    await users_collection.update_one(
        {"user_id": user_id},
        {"$inc": {"total_afk_time": additional_seconds}}
    )

async def get_top_afk_users(limit=10):
    """Get top users by total AFK time"""
    cursor = users_collection.find({"total_afk_time": {"$gt": 0}}).sort("total_afk_time", -1).limit(limit)
    return await cursor.to_list(length=limit)

# ==================== GROUP FUNCTIONS ====================

async def track_group(chat_id: int, chat_title: str):
    """Track group activity"""
    await groups_collection.update_one(
        {"chat_id": chat_id},
        {"$set": {
            "title": chat_title,
            "last_active": datetime.now()
        }},
        upsert=True
    )

async def get_all_groups():
    """Get all tracked groups"""
    groups = []
    async for group in groups_collection.find({}):
        groups.append(group)
    return groups

# ==================== AUTO-DELETE FUNCTIONS ====================

async def init_group_auto_delete_settings(chat_id: int):
    """Initialize auto-delete settings for a group"""
    settings = await auto_delete_collection.find_one({"chat_id": chat_id})
    if not settings:
        await auto_delete_collection.insert_one({
            "type": "group_settings",
            "chat_id": chat_id,
            "enabled": False,
            "delete_after": 300
        })
        logger.info(f"Auto-delete settings initialized for group {chat_id}")

async def is_auto_delete_enabled(chat_id: int) -> bool:
    """Check if auto-delete is enabled for a group"""
    settings = await auto_delete_collection.find_one({"chat_id": chat_id})
    return settings.get("enabled", False) if settings else False

async def get_auto_delete_time(chat_id: int) -> int:
    """Get auto-delete time in seconds for a group"""
    settings = await auto_delete_collection.find_one({"chat_id": chat_id})
    return settings.get("delete_after", 300) if settings else 300

async def toggle_auto_delete(chat_id: int, state: bool = None) -> bool:
    """Toggle auto-delete status for a group"""
    settings = await auto_delete_collection.find_one({"chat_id": chat_id})
    if not settings:
        await init_group_auto_delete_settings(chat_id)
        settings = await auto_delete_collection.find_one({"chat_id": chat_id})
    
    new_state = state if state is not None else not settings["enabled"]
    
    await auto_delete_collection.update_one(
        {"chat_id": chat_id},
        {"$set": {"enabled": new_state}}
    )
    logger.info(f"Auto-delete toggled to {new_state} for group {chat_id}")
    return new_state

async def set_auto_delete_time(chat_id: int, seconds: int):
    """Set auto-delete time in seconds for a group"""
    await auto_delete_collection.update_one(
        {"chat_id": chat_id},
        {"$set": {"delete_after": seconds}},
        upsert=True
    )
    logger.info(f"Auto-delete time set to {seconds // 60} minutes for group {chat_id}")

async def track_message_for_deletion(message: Message):
    """Track a message for future deletion"""
    if not message.chat or message.chat.type not in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
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

async def auto_delete_loop():
    """Background task to delete expired messages"""
    logger.info("🔄 Auto-delete task started")
    while True:
        try:
            current_time = time.time()
            query = {"type": "message", "delete_at": {"$lte": current_time}}
            messages_to_delete = await auto_delete_collection.find(query).to_list(None)
            
            for msg in messages_to_delete:
                try:
                    await app.delete_messages(msg["chat_id"], msg["message_id"])
                except Exception as e:
                    logger.error(f"Failed to delete message {msg['message_id']}: {e}")
                finally:
                    await auto_delete_collection.delete_one({"_id": msg["_id"]})
            
            await asyncio.sleep(30)
        except Exception as e:
            logger.error(f"Error in auto-delete loop: {e}")
            await asyncio.sleep(60)

async def get_auto_delete_menu(chat_id: int):
    """Generate auto-delete menu"""
    settings = await auto_delete_collection.find_one({"chat_id": chat_id})
    if not settings:
        await init_group_auto_delete_settings(chat_id)
        settings = await auto_delete_collection.find_one({"chat_id": chat_id})
    
    enabled = settings["enabled"]
    delete_after = settings["delete_after"]
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
            InlineKeyboardButton("🔙 Back", callback_data="back_to_start"),
            InlineKeyboardButton("❌ Close", callback_data=f"autodel_close:{chat_id}")
        ]
    ])
    
    return text, keyboard

# ==================== FLASK SERVER ====================

flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return "AFK Bot is running! 🚀", 200

def run_flask():
    """Run Flask server for health checks"""
    flask_app.run(host='0.0.0.0', port=PORT, debug=False)

# ==================== BOT CLASS ====================

class Bot(Client):
    """Custom Pyrogram Client"""
    
    def __init__(self):
        super().__init__(
            "afk_bot",
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=BOT_TOKEN,
            in_memory=True
        )
    
    async def start(self):
        """Start bot and send startup notification"""
        try:
            await super().start()
            me = await self.get_me()
            logger.info(f"✅ Bot started successfully: @{me.username}")
            
            if OWNER_ID and OWNER_ID > 0:
                try:
                    await self.send_message(
                        OWNER_ID,
                        f"✅ **AFK Bot Started Successfully!**\n"
                        f"🤖 Username: @{me.username}\n"
                        f"⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                    )
                    logger.info(f"✅ Startup notification sent to owner {OWNER_ID}")
                except Exception as e:
                    logger.warning(f"Could not send startup notification: {e}")
        except Exception as e:
            logger.error(f"❌ Bot startup failed: {e}")
            raise
    
    async def stop(self):
        """Stop bot gracefully"""
        await super().stop()
        logger.info("❌ Bot stopped")

# Initialize bot
app = Bot()
BOT_START_TIME = time.time()

# ==================== BOT HANDLERS ====================

@app.on_message(filters.new_chat_members)
async def new_chat_members(_, message: Message):
    """Handle when bot is added to a group"""
    if message.new_chat_members:
        for member in message.new_chat_members:
            if member.id == (await app.get_me()).id:
                await track_group(message.chat.id, message.chat.title)
                await init_group_auto_delete_settings(message.chat.id)
                logger.info(f"✅ Bot added to group: {message.chat.title} ({message.chat.id})")

@app.on_message(filters.command(["start", "help"]))
async def start_command(_, message: Message):
    """Handle /start and /help commands"""
    try:
        user = message.from_user
        if not user:
            return
        
        if message.chat.type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
            await track_group(message.chat.id, message.chat.title)
            await init_group_auto_delete_settings(message.chat.id)
        
        await add_user(user.id, user.first_name or "", user.username or "")
        
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "➕ Add to Group ➕",
                    url=f"https://t.me/{BOT_USERNAME}?startgroup=true"
                )
            ],
            [InlineKeyboardButton("Help ❓", callback_data="help")]
        ])
        
        text = f"""
╔══════════════════════════════════╗
║  🤖 AFK ADVANCE BOT 🤖          ║
║  Your Smart Away Status Manager  ║
╚══════════════════════════════════╝

👋 **Welcome {user.first_name}!**

I'm your personal AFK (Away From Keyboard) status manager. 
I'll notify everyone when you're away and automatically 
remove your AFK status when you return!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Let's get started! 🚀
"""
        
        sent_msg = await message.reply_text(text, reply_markup=keyboard)
        await track_message_for_deletion(sent_msg)
        logger.info(f"✅ /start sent to {user.id}")
        
    except Exception as e:
        logger.error(f"❌ Error in start_command: {e}")
        try:
            await message.reply_text(f"❌ Error: {str(e)[:100]}")
        except:
            pass

@app.on_callback_query(filters.regex("^help$"))
async def help_callback(_, query):
    """Handle help callback"""
    await query.answer()
    help_text = """
**📋 ALL COMMANDS**

**To set AFK:**
- `/afk` - Sets status with default message
- `/afk <reason>` - Sets status with custom reason
- `brb <reason>` - Same as /afk

**Set media AFK:**
Reply to a photo, GIF, or sticker with `/afk`

**🔔 WHAT HAPPENS WHEN YOU'RE AFK?**
- Someone mentions you → They see your AFK reason & how long you've been away
- Send any message → AFK status removed
- Bot will notify with AFK duration

**Other Commands:**
- `/stats` - View bot statistics
- `/topafk` - Show top AFK users
- `/autodel` - Configure auto-delete (groups only)
"""
    
    await query.message.edit_text(
        help_text,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("◀️ Back", callback_data="back_to_start")]]
        )
    )

@app.on_callback_query(filters.regex("^back_to_start$"))
async def back_callback(_, query):
    """Handle back to start callback"""
    await query.answer()
    user = query.from_user
    
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "➕ Add to Group ➕",
                url=f"https://t.me/{BOT_USERNAME}?startgroup=true"
            )
        ],
        [InlineKeyboardButton("Help ❔", callback_data="help")]
    ])
    
    text = f"""
╔══════════════════════════════════╗
║  🤖 AFK ADVANCE BOT 🤖          ║
║  Your Smart Away Status Manager  ║
╚══════════════════════════════════╝

👋 **Welcome {user.first_name}!**

I'm your personal AFK (Away From Keyboard) status manager. 
I'll notify everyone when you're away and automatically 
remove your AFK status when you return!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Let's get started! 🚀
"""
    
    await query.message.edit_text(text, reply_markup=keyboard)

@app.on_message(filters.command(["afk"], prefixes=["/", "!"]) | filters.regex(r"^brb\b", re.IGNORECASE))
async def afk_handler(_, message: Message):
    """Handle AFK command"""
    try:
        if message.sender_chat:
            return
        
        user_id = message.from_user.id
        user = message.from_user
        verifier, reasondb = await is_afk(user_id)
        
        if message.chat.type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
            await track_group(message.chat.id, message.chat.title)
            await init_group_auto_delete_settings(message.chat.id)
        
        await add_user(user_id, user.first_name or "", user.username or "")
        
        # Extract reason
        if message.text and message.text.lower().startswith("brb"):
            parts = message.text.split(" ", 1)
            reason_text = parts[1] if len(parts) > 1 else None
        else:
            reason_text = " ".join(message.command[1:]) if len(message.command) > 1 else None
        
        # User is returning from AFK
        if verifier:
            afk_start = reasondb["time"]
            afk_duration = int(time.time() - afk_start)
            await update_user_afk_time(user_id, afk_duration)
            await remove_afk(user_id)
            
            try:
                seenago = get_readable_time(afk_duration)
                base_text = f"**{user.first_name}** is now available again after {seenago}"
                if reasondb["reason"]:
                    base_text += f"\n\nReason: `{reasondb['reason']}`"
                
                sent_msg = await message.reply_text(base_text)
                await track_message_for_deletion(sent_msg)
                logger.info(f"✅ User {user_id} returned from AFK")
            except Exception as e:
                logger.error(f"Error in AFK return: {e}")
            return
        
        # Setting new AFK status
        details = {
            "type": "text",
            "time": time.time(),
            "data": None,
            "reason": reason_text[:100] if reason_text else None
        }
        
        await add_afk(user_id, details)
        response = f"**{user.first_name}** is now AFK"
        if details["reason"]:
            response += f"\n\nReason: `{details['reason']}`"
        
        sent_msg = await message.reply_text(response)
        await track_message_for_deletion(sent_msg)
        logger.info(f"✅ AFK set for user {user_id}")
        
    except Exception as e:
        logger.error(f"❌ Error in afk_handler: {e}")

@app.on_message(
    filters.group & ~filters.bot & ~filters.me & ~filters.service,
    group=1
)
async def afk_watcher(_, message: Message):
    """Watch for AFK users being mentioned or replying"""
    if not message.from_user:
        return
    
    try:
        userid = message.from_user.id
        user_name = message.from_user.first_name
        
        await track_group(message.chat.id, message.chat.title)
        await init_group_auto_delete_settings(message.chat.id)
        await add_user(userid, message.from_user.first_name or "", message.from_user.username or "")
        
        # Check if user is returning from AFK
        verifier, reasondb = await is_afk(userid)
        if verifier:
            if any(cmd in (message.text or message.caption or "").lower() for cmd in ["/afk", "!afk", "brb"]):
                return
            
            afk_start = reasondb["time"]
            afk_duration = int(time.time() - afk_start)
            await update_user_afk_time(userid, afk_duration)
            await remove_afk(userid)
            
            try:
                seenago = get_readable_time(afk_duration)
                base_text = f"**{user_name}** is now available again after {seenago}"
                if reasondb["reason"]:
                    base_text += f"\n\nReason: `{reasondb['reason']}`"
                
                sent_msg = await message.reply_text(base_text)
                await track_message_for_deletion(sent_msg)
            except Exception as e:
                logger.error(f"Error in AFK watcher return: {e}")
        
        # Check if replying to AFK user
        if message.reply_to_message and message.reply_to_message.from_user:
            try:
                replied_user = message.reply_to_message.from_user
                verifier, reasondb = await is_afk(replied_user.id)
                
                if verifier:
                    seenago = get_readable_time(int(time.time() - reasondb["time"]))
                    base_text = f"**{replied_user.first_name}** is AFK since {seenago}"
                    if reasondb["reason"]:
                        base_text += f"\n\nReason: `{reasondb['reason']}`"
                    
                    sent_msg = await message.reply_text(base_text)
                    await track_message_for_deletion(sent_msg)
            except Exception as e:
                logger.error(f"Error in reply watcher: {e}")
        
        # Check mentioned users
        if message.entities and message.text:
            for entity in message.entities:
                if entity.type == enums.MessageEntityType.MENTION:
                    try:
                        mentioned_text = message.text[entity.offset:entity.offset + entity.length]
                        mentioned_username = mentioned_text[1:]
                        
                        if mentioned_username.lower() == BOT_USERNAME.lower():
                            continue
                        
                        try:
                            user = await app.get_users(mentioned_username)
                        except PeerIdInvalid:
                            continue
                        
                        if user.id == message.from_user.id:
                            continue
                        
                        verifier, reasondb = await is_afk(user.id)
                        if verifier:
                            seenago = get_readable_time(int(time.time() - reasondb["time"]))
                            base_text = f"**{user.first_name}** is AFK since {seenago}"
                            if reasondb["reason"]:
                                base_text += f"\n\nReason: `{reasondb['reason']}`"
                            
                            sent_msg = await message.reply_text(base_text)
                            await track_message_for_deletion(sent_msg)
                    except Exception as e:
                        logger.error(f"Error handling mention: {e}")
    
    except Exception as e:
        logger.error(f"Error in afk_watcher: {e}")

@app.on_message(filters.command("stats"))
async def stats_command(_, message: Message):
    """Show bot statistics"""
    try:
        uptime = get_readable_time(int(time.time() - BOT_START_TIME))
        total_users = await users_collection.count_documents({})
        afk_users = await afk_collection.count_documents({})
        total_groups = await groups_collection.count_documents({})
        
        stats_text = (
            f"🤖 **Bot Statistics**\n\n"
            f"⏰ **Uptime:** `{uptime}`\n"
            f"👥 **Total Users:** `{total_users}`\n"
            f"😴 **AFK Users:** `{afk_users}`\n"
            f"👫 **Groups:** `{total_groups}`"
        )
        
        sent_msg = await message.reply_text(stats_text)
        await track_message_for_deletion(sent_msg)
    except Exception as e:
        logger.error(f"Error in stats_command: {e}")

@app.on_message(filters.command("topafk"))
async def top_afk_command(_, message: Message):
    """Show top AFK users"""
    try:
        top_users = await get_top_afk_users(10)
        
        if not top_users:
            await message.reply_text("No AFK time recorded yet.")
            return
        
        text = "🏆 **Top 10 AFK Users**\n\n"
        for idx, user in enumerate(top_users, start=1):
            total_time = user.get("total_afk_time", 0)
            first_name = user.get("first_name", "Unknown")
            username = user.get("username", "")
            name_display = f"@{username}" if username else first_name
            time_str = get_readable_time(total_time)
            text += f"{idx}. **{name_display}** – {time_str}\n"
        
        sent_msg = await message.reply_text(text)
        await track_message_for_deletion(sent_msg)
    except Exception as e:
        logger.error(f"Error in top_afk_command: {e}")

@app.on_message(filters.command(["autodel", "autodelete"]) & filters.group)
async def auto_delete_menu(_, message: Message):
    """Show auto-delete menu"""
    try:
        chat_id = message.chat.id
        await init_group_auto_delete_settings(chat_id)
        
        member = await app.get_chat_member(chat_id, message.from_user.id)
        if member.status not in [enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]:
            await message.reply_text("❌ Administrator access required")
            return
        
        text, keyboard = await get_auto_delete_menu(chat_id)
        sent_msg = await message.reply_text(text, reply_markup=keyboard)
        await track_message_for_deletion(sent_msg)
    except Exception as e:
        logger.error(f"Error in auto_delete_menu: {e}")

@app.on_callback_query(filters.regex(r"^autodel_"))
async def auto_delete_callback(_, query):
    """Handle auto-delete callbacks"""
    try:
        data = query.data
        if data.startswith("autodel_time:"):
            parts = data.split(':')
            seconds = int(parts[1])
            chat_id = int(parts[2])
            action = "time"
        else:
            parts = data.split(':')
            action = parts[0].replace("autodel_", "")
            chat_id = int(parts[1])
        
        member = await app.get_chat_member(chat_id, query.from_user.id)
        if member.status not in [enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]:
            await query.answer("❌ Admin access required", show_alert=True)
            return
        
        await query.answer()
        
        if action == "enable":
            await toggle_auto_delete(chat_id, True)
            current_time = await get_auto_delete_time(chat_id)
            minutes = current_time // 60
            text = f"✅ Auto-delete enabled!\n\n• Delete time: `{minutes} minutes`"
            await query.message.edit_text(text)
        
        elif action == "disable":
            await toggle_auto_delete(chat_id, False)
            await query.message.edit_text("❌ Auto-delete disabled")
        
        elif action == "time":
            minutes = seconds // 60
            await set_auto_delete_time(chat_id, seconds)
            await toggle_auto_delete(chat_id, True)
            await query.message.edit_text(f"✅ Auto-delete set to {minutes} minutes")
        
        elif action == "close":
            await query.message.delete()
        
        elif action == "back":
            text, keyboard = await get_auto_delete_menu(chat_id)
            await query.message.edit_text(text, reply_markup=keyboard)
    
    except Exception as e:
        logger.error(f"Error in auto_delete_callback: {e}")
        await query.answer("Error occurred", show_alert=True)

# ==================== MAIN EXECUTION ====================

async def main():
    """Main function to start the bot"""
    try:
        logger.info("=" * 60)
        logger.info("📁 Creating downloads directory...")
        os.makedirs("downloads", exist_ok=True)
        logger.info("✅ Downloads directory ready")
        
        logger.info("🔄 Starting auto-delete background task...")
        asyncio.create_task(auto_delete_loop())
        logger.info("✅ Auto-delete task started")
        
        logger.info("🌐 Starting Flask server...")
        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()
        logger.info(f"✅ Flask server started on port {PORT}")
        
        logger.info("🚀 Starting Telegram bot...")
        await app.start()
        logger.info("✅ Telegram bot started successfully")
        
        logger.info("=" * 60)
        logger.info("⏳ Bot is now idle and waiting for commands...")
        logger.info("=" * 60)
        
        await idle()
        
    except Exception as e:
        logger.error(f"❌ Fatal error in main: {e}")
        raise

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Bot stopped by user")
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")
    finally:
        logger.info("Bot shutdown complete")
