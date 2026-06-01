✨ Features
📝 Set AFK with:
💬 Text reason (/afk reason here)
🖼️ Reply to media (photo/GIF/sticker) with /afk to set media AFK
⚡ Quick AFK with /afk or brb
⏱️ AFK reason & duration shown to people who:
📢 Mention you
💬 Reply to your messages
🔄 Auto-remove AFK when you send any message
👥 Works in groups and private chats
📩 Startup notification to the bot owner
💾 MongoDB-based persistent AFK storage
🌐 Flask health check server (for uptime monitoring)
📎 Group invite button
📦 Requirements
🐍 Python 3.9+
🍃 MongoDB database
🤖 Telegram Bot Token from @BotFather
🔑 Telegram API ID & API Hash from my.telegram.org
⚙️ Environment Variables
Variable	Required	Description
BOT_TOKEN	✅ Yes	🤖 Bot token from @BotFather
API_ID	✅ Yes	📌 API ID from my.telegram.org
API_HASH	✅ Yes	🔑 API Hash from my.telegram.org
BOT_USERNAME	✅ Yes	📛 Your bot username (without @)
MONGODB_URI	✅ Yes	🍃 MongoDB connection URI
OWNER_ID	✅ Yes	👤 Your Telegram numeric ID (can get from @userinfobot)
PORT	❌ No	🌐 Flask server port (default: 8080)
🚀 Deploy
☁️ Deploy to Render (One-Click)
Deploy to Render

☁️ Deploy to Heroku (One-Click)
Deploy to Heroku

📚 Commands
Command	Description
/start or /help	📖 Show help menu
/afk	📝 Set AFK with optional reason
brb	⚡ Quick AFK with optional reason
💡 Tip: Reply to a photo, GIF, or sticker with /afk to set media AFK.
