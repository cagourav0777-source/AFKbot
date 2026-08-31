import os
import sys
import logging
import asyncio
import threading
import signal
from pyrogram import Client, idle
from bot.config import BOT_TOKEN, API_ID, API_HASH, OWNER_ID
from bot.server import run_flask
from bot.handlers import register_handlers
from bot.database import verify_database_connection, close_database

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Global flag for graceful shutdown
shutdown_event = threading.Event()


class Bot(Client):
    def __init__(self):
        super().__init__(
            "afk_bot",
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=BOT_TOKEN,
            in_memory=False
        )

    async def start(self):
        await super().start()
        logger.info("Bot client started successfully")
        register_handlers(self)
        if OWNER_ID:
            try:
                me = await self.get_me()
                await self.send_message(
                    OWNER_ID,
                    "✅ AFK Bot Started Successfully!\n"
                    f"🤖 Bot ID: {me.id if me else 'unknown'}"
                )
            except Exception as e:
                logger.error(f"Startup notification failed: {e}")

    async def stop(self):
        logger.info("Shutting down bot client...")
        await close_database()
        await super().stop()
        logger.info("Bot client stopped")


async def main():
    # Verify database connection first
    if not await verify_database_connection():
        logger.critical("Failed to connect to MongoDB. Exiting...")
        sys.exit(1)

    os.makedirs("downloads", exist_ok=True)
    logger.info("Created downloads directory")

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("Flask server started in background")

    app = Bot()
    await app.start()
    logger.info("Telegram bot is now running...")

    await idle()

    # Graceful shutdown
    await app.stop()


if __name__ == "__main__":
    try:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user (CTRL+C)")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        logger.info("Bot shutdown complete")
