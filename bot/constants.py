# Constants for the AFK Bot

# Time constants
DEFAULT_CUSTOM_AFK_DAYS = 70
MAX_AFK_DURATION_YEARS = 10
SECONDS_PER_DAY = 86400
SECONDS_PER_HOUR = 3600
SECONDS_PER_MINUTE = 60

# Broadcast delays (in seconds)
BROADCAST_GROUP_DELAY = 0.5
BROADCAST_USER_DELAY = 0.3
FLOOD_WAIT_BUFFER = 1

# Rate limits
MAX_REASON_LENGTH = 100

# Messages
MSG_WELCOME = """💤 **AFK ADVANCE BOT**
━━━━━━━━━━━━━━━━━━━━━━

👋 Hello **{name}**!

🔹 Smart AFK Management
🔹 Auto AFK Removal
🔹 AFK Duration Tracking
🔹 Media AFK Support
🔹 Live AFK Leaderboard

Stay connected, even when you're away. 🚀"""

MSG_HELP = """**📋 ALL COMMANDS**

**To set AFK:**
- `/afk [reason]` - Set AFK status with optional reason
- `brb [reason]` - Quick AFK status
- Set media AFK - Reply to a photo/GIF/sticker with `/afk`

**🔔 AFK STATS & STATUS:**
- `/check_afk` - Check your active AFK status without ending it
- `/stats` - View bot statistics
- `/topafk` or `/leaderboard` - Top 10 currently AFK users
- `/my_records` - Your personal AFK records

**👑 ADMIN COMMANDS:**
- `/broadcast` - Broadcast text or reply to media to broadcast
- `/addgroup` - Add current group to database"""

MSG_AFK_SET = "🌙 **{name}** is now AFK"
MSG_AFK_SET_REASON = "🌙 **{name}** is now AFK • `{reason}`"
MSG_AFK_ACTIVE = "💤 **{name}** is AFK for **{duration}**"
MSG_AFK_ACTIVE_REASON = "💤 **{name}** is AFK for **{duration}** • `{reason}`"
MSG_BACK_ONLINE = "✨ **{name}** is back online! (Away for: **{duration}**)"
MSG_NOT_AFK = "ℹ️ **{name}**, you are not currently AFK.\n\nUse `/afk [reason]` to go AFK."
