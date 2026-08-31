import time


def get_readable_time(seconds: int) -> str:
    """Convert seconds to human-readable time format"""
    seconds = int(seconds)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)

    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    # Only show seconds if duration is less than 1 hour
    if not days and not hours:
        parts.append(f"{seconds}s")
    elif not parts:  # If all are 0
        parts.append(f"{seconds}s")

    return " ".join(parts)


BOT_START_TIME = time.time()
