"""
AFK Bot Package

A Telegram bot for managing AFK (Away From Keyboard) status with features like:
- Smart AFK detection and tracking
- Media AFK support (photos, GIFs, stickers)
- Live leaderboards
- Personal statistics
- Admin broadcasting
"""

__version__ = "1.0.0"

from bot.config import (
    BOT_TOKEN,
    API_ID,
    API_HASH,
    BOT_USERNAME,
    MONGODB_URI,
    OWNER_ID,
    PORT
)

__all__ = [
    "BOT_TOKEN",
    "API_ID",
    "API_HASH",
    "BOT_USERNAME",
    "MONGODB_URI",
    "OWNER_ID",
    "PORT"
]
