import os


def getenv_or_raise(key, cast=str, default=None, required=False):
    val = os.getenv(key, None)
    if val is None or val == "":
        if required:
            raise RuntimeError(
                f"Environment variable {key} is required but not set.\n"
                f"Please set {key} in your environment or .env file.\n"
                f"See README.md for configuration instructions."
            )
        return default
    try:
        return cast(val) if cast is not None else val
    except Exception as e:
        raise RuntimeError(f"Failed to cast env {key}: {e}")


BOT_TOKEN = getenv_or_raise("BOT_TOKEN", required=True)
API_ID = getenv_or_raise("API_ID", cast=int, required=True)
API_HASH = getenv_or_raise("API_HASH", required=True)
BOT_USERNAME = getenv_or_raise("BOT_USERNAME", required=True)
MONGODB_URI = getenv_or_raise("MONGODB_URI", required=True)
OWNER_ID = getenv_or_raise("OWNER_ID", cast=int, default=0)
PORT = getenv_or_raise("PORT", cast=int, default=8080)
