import time
import logging
from datetime import datetime
from motor.motor_asyncio import AsyncIOMotorClient
from bot.config import MONGODB_URI

logger = logging.getLogger(__name__)

mongo_client = AsyncIOMotorClient(
    MONGODB_URI,
    maxPoolSize=50,
    minPoolSize=10,
    serverSelectionTimeoutMS=5000
)
db = mongo_client.afk_db

afk_collection = db.afk
users_collection = db.users
groups_collection = db.groups
afk_stats_collection = db.afk_stats


async def close_database():
    """Close MongoDB connection properly"""
    try:
        mongo_client.close()
        logger.info("MongoDB connection closed")
    except Exception as e:
        logger.error(f"Error closing MongoDB: {e}")


async def verify_database_connection():
    """Verify MongoDB connection at startup"""
    try:
        await mongo_client.admin.command('ping')
        logger.info("MongoDB connected successfully")
        return True
    except Exception as e:
        logger.critical(f"MongoDB connection failed: {e}")
        return False


async def add_afk(user_id: int, details: dict):
    if not isinstance(details, dict):
        return
    try:
        await afk_collection.update_one(
            {"user_id": user_id},
            {"$set": details},
            upsert=True
        )
        logger.debug(f"Added AFK for user {user_id}")
    except Exception as e:
        logger.error(f"Failed to add AFK for user {user_id}: {e}")


async def is_afk(user_id: int):
    try:
        data = await afk_collection.find_one({"user_id": user_id})
        if data:
            return True, data
        return False, None
    except Exception as e:
        logger.error(f"Failed to check AFK for user {user_id}: {e}")
        return False, None


async def remove_afk(user_id: int):
    try:
        result = await afk_collection.delete_one({"user_id": user_id})
        logger.debug(f"Removed AFK for user {user_id}: {result.deleted_count} doc(s)")
        return result.deleted_count > 0
    except Exception as e:
        logger.error(f"Failed to remove AFK for user {user_id}: {e}")
        return False


async def add_user(user_id: int, first_name: str = "", username: str = "", access_hash: int = 0):
    update_data = {
        "first_name": first_name,
        "username": username,
        "last_seen": datetime.utcnow()
    }
    if access_hash:
        update_data["access_hash"] = access_hash

    try:
        await users_collection.update_one(
            {"user_id": user_id},
            {
                "$set": update_data,
                "$setOnInsert": {"total_afk_time": 0}
            },
            upsert=True
        )
        logger.debug(f"Added/updated user {user_id}")
    except Exception as e:
        logger.error(f"Failed to add user {user_id}: {e}")


async def count_users():
    return await users_collection.count_documents({})


async def count_afk_users():
    return await afk_collection.count_documents({})


async def update_user_afk_time(user_id: int, additional_seconds: int):
    await users_collection.update_one(
        {"user_id": user_id},
        {"$inc": {"total_afk_time": additional_seconds}},
        upsert=True
    )


async def store_afk_duration(user_id: int, afk_duration: int):
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
    try:
        user_afk = await afk_stats_collection.find_one({"user_id": user_id})
        if user_afk:
            return user_afk.get("highest_afk", 0)
        return 0
    except Exception as e:
        logger.error(f"Error getting highest AFK: {e}")
        return 0


async def get_current_top_afk_users(limit: int = 10):
    try:
        cursor = afk_collection.find({}).sort("time", 1)
        results = []
        async for item in cursor:
            user_id = item.get("user_id")
            user_data = await users_collection.find_one({"user_id": user_id}) or {}

            first_name = (
                item.get("first_name")
                or item.get("name")
                or user_data.get("first_name")
                or ""
            )
            username = (
                item.get("username")
                or user_data.get("username")
                or ""
            )

            if first_name or username:
                results.append({
                    "user_id": user_id,
                    "first_name": first_name,
                    "username": username,
                    "start_time": item.get("time", time.time()),
                    "reason": item.get("reason"),
                    "type": item.get("type", "text")
                })
                if len(results) >= limit:
                    break
        return results
    except Exception as e:
        logger.error(f"Error fetching current top AFK users: {e}")
        return []


async def track_group(chat_id: int, chat_title: str, access_hash: int = 0, chat_type: str = "channel"):
    update_data = {
        "title": chat_title,
        "last_active": datetime.utcnow()
    }
    if access_hash:
        update_data["access_hash"] = access_hash
        update_data["type"] = chat_type

    try:
        await groups_collection.update_one(
            {"chat_id": chat_id},
            {"$set": update_data},
            upsert=True
        )
        logger.debug(f"Tracked group {chat_id}: {chat_title}")
    except Exception as e:
        logger.error(f"Failed to track group {chat_id}: {e}")


async def count_groups():
    return await groups_collection.count_documents({})


async def get_all_groups():
    groups = []
    async for group in groups_collection.find({}):
        groups.append(group)
    return groups
