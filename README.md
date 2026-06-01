<!-- markdownlint-disable-file -->
<p align="center">
  <a href="https://github.com/cagourav0777-source/Afk_advance-bot">
    <img src="https://img.shields.io/badge/AFK%20Advance%20Bot-v2.0-blue?style=for-the-badge&logo=telegram" alt="AFK Advance Bot">
  </a>
</p>

<h1 align="center">🤖 AFK ADVANCE BOT 2.0</h1>

<p align="center">
  <b>Your Smart Away-From-Keyboard Status Manager</b>
</p>

<p align="center">
  A powerful Telegram bot that automatically manages your AFK status, notifies people when you're away, 
  and removes your status when you return. Perfect for busy professionals, gamers, and anyone who needs smart availability management!
</p>

<p align="center">
  <a href="#features">✨ Features</a> •
  <a href="#requirements">📦 Requirements</a> •
  <a href="#installation">🚀 Installation</a> •
  <a href="#usage">📖 Usage</a> •
  <a href="#deployment">☁️ Deployment</a> •
  <a href="#support">💬 Support</a>
</p>

---

## ✨ Features

### 📝 **Smart AFK Management**
- Set AFK with custom reasons or quick shortcuts
- Automatic AFK removal when you send messages
- Media-based AFK (photos, GIFs, stickers)
- Manual AFK removal with `/back`

### 🔔 **Intelligent Notifications**
- Auto-notify when you're mentioned
- Notify when someone replies to your messages
- Show AFK reason and duration automatically
- Display media with away status

### 💾 **Persistent Storage**
- MongoDB-based storage
- AFK status persists across bot restarts
- Complete user history tracking

### 🌐 **Wide Compatibility**
- Works in private chats
- Works in group chats
- Multi-user support
- No conflicts with multiple users

### 🎨 **Beautiful UI**
- Elegant inline keyboards
- Formatted messages with emojis
- Callback-based interactive buttons
- User-friendly help system

### 📊 **Monitoring & Health Checks**
- Flask health check server
- Uptime monitoring support
- Live status endpoints
- Deployment-ready

---

## 📦 Requirements

- **Python 3.8+** - Programming language
- **MongoDB** - Database (local or cloud)
- **Telegram Bot Token** - From [@BotFather](https://t.me/BotFather)
- **Telegram API Credentials** - From [my.telegram.org](https://my.telegram.org)

### Python Libraries
- `pyrogram==2.0.106` - Telegram API client
- `motor==3.1.2` - Async MongoDB driver
- `pymongo==4.3.3` - MongoDB Python driver
- `TgCrypto==1.2.5` - TGCrypto acceleration
- `flask==3.0.0` - Web framework
- `python-dotenv==1.0.0` - Environment variables

---

## 🚀 Installation

### Step 1: Clone the Repository
```bash
git clone https://github.com/cagourav0777-source/Afk_advance-bot.git
cd Afk_advance-bot
```

### Step 2: Install Dependencies
```bash
pip install -r requirements.txt
```

### Step 3: Get Telegram Credentials
1. Create a bot with [@BotFather](https://t.me/BotFather) and get `BOT_TOKEN`
2. Visit [my.telegram.org](https://my.telegram.org) to get `API_ID` and `API_HASH`
3. Get your Telegram ID from [@userinfobot](https://t.me/userinfobot)

### Step 4: Setup MongoDB
- **Option A (Cloud):** Create free cluster at [MongoDB Atlas](https://www.mongodb.com/cloud/atlas)
- **Option B (Local):** Install MongoDB locally
- Get your `MONGODB_URI`

### Step 5: Configure Environment Variables
```bash
cp .env.example .env
```

Edit `.env` with your credentials:
```env
BOT_TOKEN=your_bot_token_here
API_ID=your_api_id_here
API_HASH=your_api_hash_here
BOT_USERNAME=your_bot_username_here
MONGODB_URI=your_mongodb_uri_here
OWNER_ID=your_telegram_id_here
PORT=8080
```

### Step 6: Run the Bot
```bash
python main.py
```

---

## 📖 Usage

### Commands

| Command | Description | Example |
|---------|-------------|----------|
| `/start` | Welcome message and features overview | `/start` |
| `/help` | Complete command guide and help | `/help` |
| `/afk [reason]` | Set AFK status (optional reason) | `/afk In a meeting` |
| `brb [time]` | Quick "Be Right Back" status | `brb 10 mins` |
| `/back` | Manually remove AFK status | `/back` |
| `/status` | Check current AFK status | `/status` |

### Setting AFK Status

**Method 1: Simple AFK**
```
/afk
```
Sets default "Away from keyboard" status.

**Method 2: Custom Message**
```
/afk In a meeting, will reply in 1 hour
```

**Method 3: Media-Based AFK**
1. Reply to a photo, GIF, or sticker
2. Send `/afk` or `/afk with message`
3. Your media will be shown as away status

**Method 4: Quick BRB**
```
brb
brb Back in 30 mins
```

### What Happens When You're AFK?

1. **When Mentioned**: People see your AFK reason and duration
2. **When Replied To**: Your status is shown automatically
3. **When You Send Message**: AFK clears instantly
4. **Media Support**: Your GIF/photo displays in notifications

### Clearing AFK

**Method 1: Send Any Message**
- Just type anything and AFK clears automatically!

**Method 2: Manual Clear**
```
/back
```

**Method 3: Check Status**
```
/status
```

---

## ☁️ Deployment

### Deploy to Render

1. Fork this repository
2. Create account on [Render](https://render.com)
3. Click "New +" → "Web Service"
4. Connect GitHub repository
5. Add environment variables
6. Deploy!

**Environment Variables for Render:**
- `BOT_TOKEN`
- `API_ID`
- `API_HASH`
- `BOT_USERNAME`
- `MONGODB_URI`
- `OWNER_ID`
- `PORT=8080`

### Deploy to Railway

1. Create account on [Railway](https://railway.app)
2. Connect GitHub repository
3. Add environment variables
4. Deploy automatically!

### Deploy to Heroku

1. Create account on [Heroku](https://heroku.com)
2. Install [Heroku CLI](https://devcenter.heroku.com/articles/heroku-cli)
3. Run:
```bash
heroku login
heroku create your-app-name
git push heroku main
```

### Deploy to VPS

1. SSH into your VPS
2. Install Python 3.8+
3. Clone repository
4. Install dependencies: `pip install -r requirements.txt`
5. Run: `python main.py`
6. Use `screen` or `systemd` to keep running

---

## 🏗️ Project Structure

```
Afk_advance-bot/
├── main.py              # Main bot application
├── config.py            # Configuration and environment setup
├── database.py          # MongoDB operations
├── handlers.py          # Command and message handlers
├── callbacks.py         # Inline button callbacks
├── health_server.py     # Flask health check server
├── requirements.txt     # Python dependencies
├── .env.example         # Environment variables example
├── README.md            # Documentation
└── Dockerfile           # Docker configuration
```

---

## 💡 Pro Tips

### Creative AFK Messages
```
/afk 🎮 Gaming Mode - Don't Disturb 🎮
/afk 😴 Sleeping, back tomorrow
/afk 📞 In a call, will text back
/afk ✈️ On vacation! Back 25th June
```

### Media AFK
- Reply to funny memes with `/afk`
- Use custom stickers as away status
- Share GIFs to show you're away

### Duration Tracking
Bot automatically shows:
- `5m` = 5 minutes away
- `2h 30m` = 2 hours 30 minutes
- `1d 3h 15m` = 1 day 3 hours 15 minutes

### Group Usage
- Bot works automatically in groups
- Notifies when you're mentioned
- Alerts on message replies
- No need for manual activation

---

## ⚙️ Configuration

### Database Collections

**AFK Records Collection**
```json
{
  "user_id": 123456789,
  "reason": "In a meeting",
  "media_type": "photo",
  "media_id": "file_id_here",
  "set_at": "2024-01-10T12:30:00Z",
  "is_afk": true
}
```

**User Status Collection**
```json
{
  "user_id": 123456789,
  "first_name": "John",
  "username": "johndoe",
  "last_seen": "2024-01-10T12:30:00Z"
}
```

---

## 🐛 Troubleshooting

### Bot doesn't start
- Check all environment variables in `.env`
- Verify MongoDB URI is correct
- Ensure bot token is valid

### Database connection fails
- Check MongoDB URI format
- Verify internet connection
- Ensure MongoDB service is running

### No notifications received
- Check if bot has permissions in groups
- Verify mentions are correct
- Check bot's privacy settings

### Messages not auto-clearing AFK
- Make sure you're sending text (not forwarding)
- Avoid command messages (/afk, /back)
- Check database connection

---

## 📊 API Endpoints (Health Check)

### `/health`
Returns bot health status
```json
{
  "status": "healthy",
  "bot": "AFK Advance Bot",
  "version": "2.0.0"
}
```

### `/status`
Returns bot operational status
```json
{
  "online": true,
  "version": "2.0.0",
  "service": "afk-advance-bot"
}
```

---

## 📝 License

This project is licensed under the **MIT License** - see LICENSE file for details.

---

## 🙏 Acknowledgments

- Built with [Pyrogram](https://pyrogram.org)
- Database powered by [MongoDB](https://mongodb.com)
- Inspired by community needs

---

## 💬 Support & Contribution

### Need Help?
- 📧 Open an issue on GitHub
- 💬 Join support community
- 📚 Read documentation

### Want to Contribute?
1. Fork the repository
2. Create a feature branch
3. Commit your changes
4. Push to the branch
5. Open a Pull Request

### Report Issues
- Describe the problem clearly
- Include error messages
- Provide steps to reproduce
- Share environment details

---

## 🌟 Show Your Support

If you find this bot helpful:
- ⭐ Star this repository
- 🔄 Share with friends
- 💬 Leave feedback
- 🐛 Report bugs

---

<p align="center">
  <b>Made with ❤️ for Telegram Users</b>
</p>

<p align="center">
  <a href="https://github.com/cagourav0777-source/Afk_advance-bot">
    GitHub Repository
  </a> •
  <a href="https://t.me/team_secrat_bots">
    Support Group
  </a>
</p>
