from flask import Flask
from bot.config import PORT

flask_app = Flask(__name__)


@flask_app.route('/')
def home():
    return "AFK Bot is running! 🚀", 200


def run_flask():
    flask_app.run(host='0.0.0.0', port=PORT, threaded=True, use_reloader=False)
