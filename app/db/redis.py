import redis
from app.core.config import settings

# Redis client
redis_client = redis.from_url(settings.redis_url)


def get_redis_client():
    """Dependency to get Redis client."""
    return redis_client


def enqueue_task(queue_name: str, task_data: dict):
    """Simple helper to enqueue a task in Redis."""
    redis_client.lpush(queue_name, str(task_data))


def ping_redis() -> bool:
    """Ping Redis to check connection."""
    try:
        return redis_client.ping()
    except redis.ConnectionError:
        return False