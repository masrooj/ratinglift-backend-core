from fastapi import Request
from sqlalchemy.orm import Session
from pymongo.database import Database
import redis

from app.db.session import get_db
from app.db.mongo import get_mongo_db
from app.db.redis import get_redis_client


def get_request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "")


def get_tenant_id(request: Request) -> str:
    return getattr(request.state, "tenant_id", "anonymous")


def get_database_session() -> Session:
    """Dependency to get SQLAlchemy database session."""
    return next(get_db())


def get_mongo_database() -> Database:
    """Dependency to get MongoDB database."""
    return get_mongo_db()


def get_redis() -> redis.Redis:
    """Dependency to get Redis client."""
    return get_redis_client()
