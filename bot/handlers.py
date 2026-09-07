import os
import time
import re
import logging
import asyncio
from pyrogram import Client, filters, enums
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery
)
from pyrogram.errors import (
    FloodWait,
    UserIsBlocked,
    InputUserDeactivated,
    PeerIdInvalid,
    ChatWriteForbidden,
    ChannelPrivate
)
from bot.config import BOT_USERNAME, OWNER_ID
from bot.utils import get_readable_time, BOT_START_TIME
from bot.database import (
    is_afk, add_afk, remove_afk, add_user, track_group,
    update_user_afk_time, store_afk_duration,
    get_current_top_afk_users, count_users, count_afk_users, count_groups,
    get_all_groups,
    users_collection, groups_collection, afk_stats_collection, afk_collection
)
from bot.constants import (
    DEFAULT_CUSTOM_AFK_DAYS, MAX_AFK_DURATION_YEARS, SECONDS_PER_DAY,
    BROADCAST_GROUP_DELAY, BROADCAST_USER_DELAY, FLOOD_WAIT_BUFFER,
    MAX_REASON_LENGTH, MSG_WELCOME, MSG_HELP, MSG_AFK_SET, MSG_AFK_SET_REASON,
    MSG_AFK_ACTIVE, MSG_AFK_ACTIVE_REASON, MSG_BACK_ONLINE, MSG_NOT_AFK
)

logger = logging.getLogger(__name__)

# Compile regex at module level for efficiency
TIME_UNIT_PATTERN = re.compile(r'(\d+(?:\.\d+)?)\s*([a-zA-Z]+)', re.IGNORECASE)

# Global lock for broadcast to prevent duplicate sends
broadcast_lock = asyncio.Lock()


def parse_custom_duration(text: str):
    """
    Parses duration and reason cleanly using string partitions.
    Supports bracketed reason: /custom_afk 120 Days 20 hours [Dead Person]
    Also supports: /custom_afk 120 days 20h Dead Person, /custom_afk 70
    """
    text = (text or "").strip()
    if not text:
        return DEFAULT_CUSTOM_AFK_DAYS * SECONDS_PER_DAY, None

    time_units = {
        'd': SECONDS_PER_DAY, 'day': SECONDS_PER_DAY, 'days': SECONDS_PER_DAY,
        'h': 3600, 'hr': 3600, 'hrs': 3600, 'hour': 3600, 'hours': 3600,
        'm': 60, 'min': 60, 'mins': 60, 'minute': 60, 'minutes': 60,
        's': 1, 'sec': 1, 'secs': 1, 'second': 1, 'seconds': 1
    }

    reason = None
    time_text = text

    bracket_match = re.search(r'\[(.*?)\]', text)
    if bracket_match:
        reason = bracket_match.group(1).strip() or None
        time_text = re.sub(r'\[(.*?)\]', '', text).strip()

    matches = list(TIME_UNIT_PATTERN.finditer(time_text))

    total_seconds = 0
    matched_ranges = []

    for m in matches:
        val = float(m.group(1))
        unit = m.group(2).lower()
        if unit in time_units:
            total_seconds += int(val * time_units[unit])
            matched_ranges.append(m.span())

    # Validate max duration
    max_seconds = MAX_AFK_DURATION_YEARS * 365 * SECONDS_PER_DAY
    if total_seconds > max_seconds:
        logger.warning(f"Duration {total_seconds}s exceeds max, capping to {max_seconds}s")
        total_seconds = max_seconds

    if total_seconds > 0:
        if not reason:
            cleaned = time_text
            for start, end in reversed(matched_ranges):
                cleaned = cleaned[:start] + " " + cleaned[end:]
            rem = " ".join(cleaned.split()).strip()
            reason = rem if rem else None
        return total_seconds, reason

    first_token, _, remaining_text = time_text.partition(" ")
    try:
        val = float(first_token)
        total_seconds = int(val * SECONDS_PER_DAY)
        if total_seconds > max_seconds:
            total_seconds = max_seconds
        if not reason and remaining_text:
            reason = remaining_text.strip() or None
        return total_seconds, reason
    except ValueError:
        pass

    return DEFAULT_CUSTOM_AFK_DAYS * SECONDS_PER_DAY, (reason or text)


async def send_afk_message(message, afktype, data, reasonafk, timeafk, user_name, is_return=False):
    """Unified function to send AFK status or welcome back message"""
    seenago = get_readable_time(int(time.time() - float(timeafk))) if timeafk else "some time"

    if is_return:
        base_text = MSG_BACK_ONLINE.format(name=user_name, duration=seenago)
    else:
        if reasonafk:
            base_text = MSG_AFK_ACTIVE_REASON.format(name=user_name, duration=seenago, reason=reasonafk)
        else:
            base_text = MSG_AFK_ACTIVE.format(name=user_name, duration=seenago)

    try:
        if afktype == "animation" and data:
            await message.reply_animation(data, caption=base_text)
        elif afktype == "photo" and data:
            await message.reply_photo(photo=data, caption=base_text)
        elif afktype == "sticker" and data:
            await message.reply_sticker(sticker=data)
            await asyncio.sleep(0.5)
            await message.reply_text(base_text)
        else:
            await message.reply_text(base_text, disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"Failed to send AFK message with media: {e}")
        await message.reply_text(base_text, disable_web_page_preview=True)


def register_handlers(app: Client):

    @app.on_message(filters.new_chat_members)
    async def new_chat_members(_, message: Message):
        if not message or not message.new_chat_members:
            return
        me = await app.get_me()
        for member in message.new_chat_members:
            if member.id == me.id:
                await track_group(message.chat.id, message.chat.title)
                logger.info(f"Bot added to group: {message.chat.title} ({message.chat.id})")

    @app.on_message(filters.command(["start"]))
    async def start_command(_, message: Message):
        user = message.from_user
        if not user:
            return

        if message.chat and message.chat.type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
            await track_group(message.chat.id, message.chat.title)

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

        user_name = user.first_name or "User"
        text = MSG_WELCOME.format(name=user_name)

        await message.reply_text(
            text,
            reply_markup=keyboard,
            disable_web_page_preview=True
        )

    @app.on_callback_query(filters.regex("^help$"))
    async def help_callback(_, query: CallbackQuery):
        await query.answer()

        try:
            await query.message.edit_text(
                MSG_HELP,
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("◀️ Back", callback_data="back_to_start")]]
                ),
                disable_web_page_preview=True,
            )
        except Exception as e:
            logger.error(f"Failed to edit help message: {e}")
            await query.answer("Help shown", show_alert=True)

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

        user_name = user.first_name or "User"
        text = MSG_WELCOME.format(name=user_name)

        try:
            await query.message.edit_text(
                text,
                reply_markup=keyboard,
                disable_web_page_preview=True
            )
        except Exception as e:
            logger.error(f"Failed to edit start message: {e}")
            await query.message.reply_text(
                text,
                reply_markup=keyboard,
                disable_web_page_preview=True
            )

    # ------------------ SECRET OWNER COMMAND ------------------
    @app.on_message(filters.command(["custom_afk", "setafk"], prefixes=["/", "!"]))
    async def custom_afk_command(_, message: Message):
        if not OWNER_ID or not message.from_user or message.from_user.id != OWNER_ID:
            return

        user = message.from_user
        user_id = user.id

        try:
            await message.delete()
        except Exception:
            pass

        _, _, args_text = (message.text or "").partition(" ")
        args_text = args_text.strip()

        back_seconds, reason_text = parse_custom_duration(args_text)
        custom_start_time = time.time() - back_seconds

        details = {
            "type": "text",
            "time": custom_start_time,
            "data": None,
            "reason": (reason_text[:MAX_REASON_LENGTH] if reason_text else None),
            "first_name": user.first_name or "",
            "username": user.username or ""
        }

        await add_afk(user_id, details)
        await add_user(user_id, user.first_name or "", user.username or "")

        readable_time = get_readable_time(back_seconds)
        response = (
            f"👑 <b>Secret AFK Activated (Owner Only)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 <b>User:</b> {user.first_name}\n"
            f"⏱️ <b>Set Duration:</b> <code>{readable_time}</code>\n"
        )
        if reason_text:
            response += f"📝 <b>Reason:</b> <code>{reason_text}</code>\n"
        response += (
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔴 <b>Status:</b> Active (Time will advance naturally)"
        )

        try:
            await message.reply_text(response, parse_mode=enums.ParseMode.HTML)
        except Exception:
            await app.send_message(message.chat.id, response, parse_mode=enums.ParseMode.HTML)

    @app.on_message(filters.command(["check_afk", "checkafk"], prefixes=["/", "!"]))
    async def check_afk_command(_, message: Message):
        if not message or not message.from_user:
            return

        user = message.from_user
        user_id = user.id
        user_name = user.first_name or "User"

        verifier, reasondb = await is_afk(user_id)

        try:
            await message.delete()
        except Exception:
            pass

        if not verifier or not reasondb:
            await message.reply_text(MSG_NOT_AFK.format(name=user_name))
            return

        afk_start = reasondb.get("time", time.time())
        reasonafk = reasondb.get("reason", None)
        current_duration = get_readable_time(int(time.time() - float(afk_start))) if afk_start else "some time"

        text = (
            f"💤 <b>Your AFK Status</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 <b>User:</b> {user_name}\n"
            f"⏱️ <b>Away For:</b> <code>{current_duration}</code>\n"
        )
        if reasonafk:
            text += f"📝 <b>Reason:</b> <code>{reasonafk}</code>\n"
        text += (
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔴 <b>Status:</b> Still AFK"
        )

        await message.reply_text(text, parse_mode=enums.ParseMode.HTML)

    @app.on_message(filters.command("my_records"))
    async def my_records_command(_, message: Message):
        try:
            await message.delete()
        except Exception:
            pass

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
            f"📊 <b>Average Duration:</b>\n"
            f"   <code>{get_readable_time(avg_afk)}</code>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💎 Keep tracking your AFK journey!"
        )

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Close", callback_data="close_message")]
        ])

        await message.reply_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=keyboard)

    @app.on_callback_query(filters.regex("^close_message$"))
    async def close_message_callback(_, query: CallbackQuery):
        await query.answer()
        try:
            await query.message.delete()
        except Exception:
            pass

    # ------------------ UPDATED TOP AFK / LEADERBOARD ------------------
    @app.on_message(filters.command(["topafk", "leaderboard"]))
    async def top_afk_command(_, message: Message):
        try:
            await message.delete()
        except Exception:
            pass

        top_users = await get_current_top_afk_users(10)

        if not top_users:
            await message.reply_text("💤 **No users are currently AFK!**")
            return

        # 1. Top users ke IDs collect karo taaki live data fetch ho sake
        user_ids = [u.get("user_id") for u in top_users if u.get("user_id")]
        live_users_map = {}
        if user_ids:
            try:
                fetched = await app.get_users(user_ids)
                if not isinstance(fetched, list):
                    fetched = [fetched]
                for fu in fetched:
                    live_users_map[fu.id] = fu
            except Exception as e:
                logger.warning(f"Could not batch fetch live users for leaderboard: {e}")

        text = "🏆 **Top 10 Currently AFK Users**\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
        for idx, user in enumerate(top_users, start=1):
            user_id = user.get("user_id")
            start_time = user.get("start_time", time.time())
            reason = user.get("reason")

            # Live info agar Telegram server se mil gayi ho
            live_user = live_users_map.get(user_id)
            if live_user:
                if getattr(live_user, "is_deleted", False):
                    first_name = "Deleted Account"
                    username = ""
                else:
                    first_name = live_user.first_name or "User"
                    username = live_user.username or ""

                # Background me DB ko fresh username/name ke sath update kar do
                asyncio.create_task(
                    afk_collection.update_one(
                        {"user_id": user_id},
                        {"$set": {"first_name": first_name, "username": username}}
                    )
                )
                asyncio.create_task(
                    users_collection.update_one(
                        {"user_id": user_id},
                        {"$set": {"first_name": first_name, "username": username}}
                    )
                )
            else:
                first_name = user.get("first_name") or "User"
                username = user.get("username") or ""

            current_duration = int(time.time() - float(start_time)) if start_time else 0
            readable_time = get_readable_time(current_duration)

            # Permanent clickable link using tg://user?id= (profile hamesha open hogi)
            if user_id:
                name_display = f"[{first_name}](tg://user?id={user_id})"
            else:
                name_display = first_name

            # Agar valid username active hai toh sath me dikhao
            if username:
                name_display += f" (@{username})"

            reason_str = f" | `{reason}`" if reason else ""

            if idx == 1:
                text += f"🥇 {idx}. **{name_display}** — `{readable_time}`{reason_str}\n"
            elif idx == 2:
                text += f"🥈 {idx}. **{name_display}** — `{readable_time}`{reason_str}\n"
            elif idx == 3:
                text += f"🥉 {idx}. **{name_display}** — `{readable_time}`{reason_str}\n"
            else:
                text += f"🔹 {idx}. **{name_display}** — `{readable_time}`{reason_str}\n"

        await message.reply_text(text, disable_web_page_preview=True)

    @app.on_message(filters.command(["afk"], prefixes=["/", "!"]) | filters.regex(r"^brb\b", re.IGNORECASE))
    async def afk_handler(_, message: Message):
        if not message or getattr(message, "sender_chat", None):
            return

        user = message.from_user
        if not user:
            return

        user_id = user.id
        user_name = user.first_name or "User"

        try:
            await message.delete()
        except Exception:
            pass

        if message.chat and message.chat.type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
            await track_group(message.chat.id, message.chat.title)

        await add_user(user_id, user_name, user.username or "")

        reason_text = None
        if message.text and message.text.lower().startswith("brb"):
            _, _, rem = message.text.partition(" ")
            reason_text = rem.strip() if rem else None
        else:
            _, _, rem = (message.text or "").partition(" ")
            reason_text = rem.strip() if rem else None

        verifier, reasondb = await is_afk(user_id)

        if verifier and reasondb:
            afk_start = reasondb.get("time", time.time())
            try:
                afk_duration = int(time.time() - float(afk_start))
            except Exception:
                afk_duration = 0

            await store_afk_duration(user_id, afk_duration)
            await update_user_afk_time(user_id, afk_duration)
            await remove_afk(user_id)

            try:
                afktype = reasondb.get("type", "text")
                timeafk = reasondb.get("time", afk_start)
                data = reasondb.get("data", None)
                reasonafk = reasondb.get("reason", None)
                await send_afk_message(message, afktype, data, reasonafk, timeafk, user_name, is_return=True)
            except Exception as e:
                logger.error(f"Error in AFK return: {e}")
                seenago = get_readable_time(int(time.time() - float(afk_start))) if afk_start else "some time"
                await message.reply_text(
                    MSG_BACK_ONLINE.format(name=user_name, duration=seenago),
                    disable_web_page_preview=True
                )
            return

        details = {
            "type": "text",
            "time": time.time(),
            "data": None,
            "reason": (reason_text[:MAX_REASON_LENGTH] if reason_text else None),
            "first_name": user_name,
            "username": user.username or ""
        }

        try:
            if message.animation:
                details.update({"type": "animation", "data": message.animation.file_id, "time": time.time()})
            elif message.photo:
                try:
                    file_id = message.photo[-1].file_id if isinstance(message.photo, (list, tuple)) else message.photo.file_id
                    details.update({"type": "photo", "data": file_id, "time": time.time()})
                except Exception:
                    details.update({"type": "photo", "data": None, "time": time.time()})
            elif message.reply_to_message:
                rm = message.reply_to_message
                if rm.animation:
                    details.update({"type": "animation", "data": rm.animation.file_id, "time": time.time()})
                elif rm.photo:
                    try:
                        file_id = rm.photo[-1].file_id if isinstance(rm.photo, (list, tuple)) else rm.photo.file_id
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

        if details.get("reason"):
            response = MSG_AFK_SET_REASON.format(name=user_name, reason=details['reason'])
        else:
            response = MSG_AFK_SET.format(name=user_name)

        await message.reply_text(response)

    @app.on_message(filters.group, group=0)
    async def group_tracker(_, message: Message):
        if message.chat:
            await track_group(message.chat.id, message.chat.title)

    @app.on_message(
        filters.group & ~filters.bot & ~filters.me & ~filters.service,
        group=1
    )
    async def afk_watcher(_, message: Message):
        if not message or not message.from_user:
            return

        userid = message.from_user.id
        user_name = message.from_user.first_name or "User"

        await add_user(userid, user_name, message.from_user.username or "")

        verifier, reasondb = await is_afk(userid)
        if verifier and reasondb:
            text_lower = ((message.text or "") + " " + (message.caption or "")).lower()
            if any(cmd in text_lower for cmd in ["/afk", "!afk", "brb", "/check_afk", "!check_afk", "/checkafk", "/custom_afk", "!custom_afk", "/setafk", "!setafk"]):
                return

            afk_start = reasondb.get("time", time.time())
            try:
                afk_duration = int(time.time() - float(afk_start))
            except Exception:
                afk_duration = 0

            await store_afk_duration(userid, afk_duration)
            await update_user_afk_time(userid, afk_duration)
            await remove_afk(userid)

            try:
                afktype = reasondb.get("type", "text")
                timeafk = reasondb.get("time", afk_start)
                data = reasondb.get("data")
                reasonafk = reasondb.get("reason")
                await send_afk_message(message, afktype, data, reasonafk, timeafk, user_name, is_return=True)
            except Exception as e:
                logger.error(f"Error in AFK return watcher: {e}")
                await message.reply_text(MSG_BACK_ONLINE.format(name=user_name, duration="some time"))

        # 1. Reply to AFK User
        if message.reply_to_message and message.reply_to_message.from_user:
            try:
                replied_user = message.reply_to_message.from_user
                replied_user_name = replied_user.first_name or "User"
                verifier, reasondb = await is_afk(replied_user.id)

                if verifier and reasondb:
                    await add_user(replied_user.id, replied_user_name, replied_user.username or "")
                    await afk_collection.update_one(
                        {"user_id": replied_user.id},
                        {"$set": {
                            "first_name": replied_user_name,
                            "username": replied_user.username or ""
                        }}
                    )

                    afktype = reasondb.get("type", "text")
                    timeafk = reasondb.get("time", time.time())
                    data = reasondb.get("data")
                    reasonafk = reasondb.get("reason")
                    await send_afk_message(message, afktype, data, reasonafk, timeafk, replied_user_name, is_return=False)
            except Exception as e:
                logger.error(f"Error in AFK reply watcher: {e}")

        # 2. Mention / Tag of AFK User
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
                        mentioned_user_name = user_obj.first_name or "User"
                        verifier, reasondb = await is_afk(user_obj.id)
                        if verifier and reasondb:
                            await add_user(user_obj.id, mentioned_user_name, user_obj.username or "")
                            await afk_collection.update_one(
                                {"user_id": user_obj.id},
                                {"$set": {
                                    "first_name": mentioned_user_name,
                                    "username": user_obj.username or ""
                                }}
                            )
                            afktype = reasondb.get("type", "text")
                            timeafk = reasondb.get("time", time.time())
                            data = reasondb.get("data")
                            reasonafk = reasondb.get("reason")
                            await send_afk_message(message, afktype, data, reasonafk, timeafk, mentioned_user_name, is_return=False)

                    elif entity.type == enums.MessageEntityType.TEXT_MENTION:
                        user_obj = entity.user
                        if not user_obj or user_obj.id == message.from_user.id:
                            continue
                        mentioned_user_name = user_obj.first_name or "User"
                        verifier, reasondb = await is_afk(user_obj.id)
                        if verifier and reasondb:
                            await add_user(user_obj.id, mentioned_user_name, user_obj.username or "")
                            await afk_collection.update_one(
                                {"user_id": user_obj.id},
                                {"$set": {
                                    "first_name": mentioned_user_name,
                                    "username": user_obj.username or ""
                                }}
                            )
                            afktype = reasondb.get("type", "text")
                            timeafk = reasondb.get("time", time.time())
                            data = reasondb.get("data")
                            reasonafk = reasondb.get("reason")
                            await send_afk_message(message, afktype, data, reasonafk, timeafk, mentioned_user_name, is_return=False)
                except Exception as e:
                    logger.error(f"Error handling mention: {e}")

    @app.on_message(filters.command("stats"))
    async def stats_command(_, message: Message):
        try:
            await message.delete()
        except Exception:
            pass

        uptime = get_readable_time(int(time.time() - BOT_START_TIME))
        total_users = await count_users()
        afk_users = await count_afk_users()
        total_groups = await count_groups()

        stats_text = (
            f"🤖 **Bot Statistics**\n"
            f"• Uptime: `{uptime}`\n"
            f"• Total Users: `{total_users}`\n"
            f"• Current AFK Users: `{afk_users}`\n"
            f"• Groups Added: `{total_groups}`"
        )

        await message.reply_text(stats_text)

    @app.on_message(filters.command("addgroup"))
    async def add_group_command(_, message: Message):
        if not OWNER_ID or message.from_user.id != OWNER_ID:
            await message.reply_text("❌ This command is only for the bot owner.")
            return

        try:
            await message.delete()
        except Exception:
            pass

        if message.chat and message.chat.type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
            await track_group(message.chat.id, message.chat.title)
            await message.reply_text(f"✅ Group **{message.chat.title}** added to database.")
        else:
            await message.reply_text("❌ Use this command in a group to add it to the database.")

    @app.on_message(filters.command("broadcast"))
    async def broadcast_command(_, message: Message):
        if not OWNER_ID or message.from_user.id != OWNER_ID:
            await message.reply_text("❌ This command is only for the bot owner.")
            return

        if broadcast_lock.locked():
            await message.reply_text("⚠️ A broadcast is already in progress. Please wait.")
            return

        async with broadcast_lock:
            broadcast_msg = message.reply_to_message
            broadcast_text = None

            if not broadcast_msg:
                _, _, broadcast_text = (message.text or "").partition(" ")
                broadcast_text = broadcast_text.strip()
                if not broadcast_text:
                    await message.reply_text(
                        "❌ Please provide a message to broadcast.\n\n"
                        "Usage: `/broadcast Your message here`\n"
                        "Or reply to any text/media message with `/broadcast`"
                    )
                    return

            groups = await get_all_groups()

            unique_groups = list({g["chat_id"]: g for g in groups if "chat_id" in g}.values())

            total_sent = 0
            total_failed = 0
            blocked_users = 0
            uncached_peers = 0
            invalid_groups = []
            invalid_users = []

            status_msg = await message.reply_text(
                f"📢 **Broadcast Started**\n\nSending to {len(unique_groups)} groups and users..."
            )

            # 1. Send to groups
            for group in unique_groups:
                group_id = group.get("chat_id")
                if not group_id:
                    continue

                try:
                    if broadcast_msg:
                        await broadcast_msg.copy(group_id)
                    else:
                        await app.send_message(group_id, broadcast_text)
                    total_sent += 1
                    await asyncio.sleep(BROADCAST_GROUP_DELAY)
                except FloodWait as e:
                    logger.warning(f"FloodWait {e.value}s for group {group_id}")
                    await asyncio.sleep(e.value + FLOOD_WAIT_BUFFER)
                    try:
                        if broadcast_msg:
                            await broadcast_msg.copy(group_id)
                        else:
                            await app.send_message(group_id, broadcast_text)
                        total_sent += 1
                    except Exception as retry_err:
                        logger.error(f"Retry failed for group {group_id}: {retry_err}")
                        total_failed += 1
                except (ChatWriteForbidden, ChannelPrivate):
                    invalid_groups.append(group_id)
                    total_failed += 1
                except PeerIdInvalid:
                    uncached_peers += 1
                    total_failed += 1
                except Exception as e:
                    logger.error(f"Failed to send to group {group_id}: {e}")
                    total_failed += 1

            # 2. Send to users
            user_count = 0
            async for user in users_collection.find({}):
                user_id = user.get("user_id")
                if not user_id:
                    continue

                user_count += 1

                try:
                    if broadcast_msg:
                        await broadcast_msg.copy(user_id)
                    else:
                        await app.send_message(user_id, broadcast_text)
                    total_sent += 1
                    await asyncio.sleep(BROADCAST_USER_DELAY)
                except FloodWait as e:
                    logger.warning(f"FloodWait {e.value}s for user {user_id}")
                    await asyncio.sleep(e.value + FLOOD_WAIT_BUFFER)
                    try:
                        if broadcast_msg:
                            await broadcast_msg.copy(user_id)
                        else:
                            await app.send_message(user_id, broadcast_text)
                        total_sent += 1
                    except Exception as retry_err:
                        logger.error(f"Retry failed for user {user_id}: {retry_err}")
                        total_failed += 1
                except UserIsBlocked:
                    blocked_users += 1
                    invalid_users.append(user_id)
                    total_failed += 1
                except InputUserDeactivated:
                    invalid_users.append(user_id)
                    total_failed += 1
                except PeerIdInvalid:
                    uncached_peers += 1
                    total_failed += 1
                except Exception as e:
                    logger.error(f"Failed to send to user {user_id}: {e}")
                    total_failed += 1

            if invalid_groups:
                await groups_collection.delete_many({"chat_id": {"$in": invalid_groups}})
                logger.info(f"Cleaned up {len(invalid_groups)} invalid groups")

            if invalid_users:
                await users_collection.delete_many({"user_id": {"$in": invalid_users}})
                logger.info(f"Cleaned up {len(invalid_users)} invalid users")

            summary = (
                f"✅ **Broadcast Completed**\n\n"
                f"📊 **Statistics:**\n"
                f"• ✅ Sent: `{total_sent}`\n"
                f"• ❌ Failed: `{total_failed}`\n"
                f"• 🚫 Blocked Users: `{blocked_users}`\n"
                f"• ⏳ Uncached (Session restart): `{uncached_peers}`\n\n"
                f"📋 **Targeted:**\n"
                f"• Groups: `{len(unique_groups)}`\n"
                f"• Users: `{user_count}`"
            )

            if invalid_groups or invalid_users:
                summary += f"\n\n🧹 **Cleaned up:** {len(invalid_groups)} inactive groups & {len(invalid_users)} blocked users"

            await status_msg.edit_text(summary)
