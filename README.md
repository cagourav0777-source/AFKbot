# 🤖 Advanced AFK Telegram Bot

A lightweight and asynchronous Telegram bot built with **Pyrogram**, **Motor (MongoDB)**, and **Flask** to manage AFK (Away From Keyboard) statuses, track live AFK durations, display active leaderboards, and broadcast messages.

---

## ✨ Features

- 💤 **Smart AFK Triggers**: Set status using `/afk [reason]`, `brb [reason]`, or reply to any photo, GIF, or sticker.
- ⏱️ **Live AFK Tracking**: Displays exact away time when someone mentions or replies to an AFK user.
- 🔄 **Auto-Return**: Automatically marks users as available when they send a message.
- 🏆 **Live Leaderboard**: `/topafk` / `/leaderboard` displays the top 10 currently active AFK users.
- 📢 **Admin Broadcast**: Send formatted text or rich media (photos, videos, stickers) with automatic `FloodWait` handling and database cleanup.
- 🌐 **Uptime Webhook**: Built-in Flask server for 24/7 health check monitoring on platforms like Railway, Render, or Koyeb.

---

## 🛠️ Environment Variables

| Variable | Required | Description |
| :--- | :--- | :--- |
| `API_ID` | Yes | Telegram API ID from [my.telegram.org](https://my.telegram.org) |
| `API_HASH` | Yes | Telegram API Hash from [my.telegram.org](https://my.telegram.org) |
| `BOT_TOKEN` | Yes | Bot token from [@BotFather](https://t.me/BotFather) |
| `BOT_USERNAME` | Yes | Bot username without `@` |
| `MONGODB_URI` | Yes | MongoDB connection string |
| `OWNER_ID` | Yes | Numeric Telegram ID of the bot owner |
| `PORT` | No | Port for Flask web server (default: `8080`) |

---

## 📖 Commands

### User Commands
- `/afk [reason]` – Set AFK status with an optional reason.
- `brb [reason]` – Quick AFK shortcut.
- `/my_records` – View your personal all-time AFK statistics.
- `/topafk` or `/leaderboard` – View top 10 currently AFK users.
- `/stats` – View bot uptime and user/group statistics.
- `/start` or `/help` – Show bot information and help menu.

### Admin Commands (Owner Only)
- `/broadcast [message]` – Broadcast text to all users and groups.
- Reply with `/broadcast` – Broadcast any replied media/message to all users and groups.
- `/addgroup` – Add current group to the database.

---

## 🚀 Quick Deployment (Railway / VPS)

1. Clone this repository:
   ```bash
   git clone [https://github.com/your-username/afk-telegram-bot.git](https://github.com/your-username/afk-telegram-bot.git)
   cd afk-telegram-bot
