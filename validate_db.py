#!/usr/bin/env python3
"""Validation script to test database connections and setup."""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings
from app.db.session import engine
from app.db.mongo import client as mongo_client
from app.db.redis import redis_client, ping_redis
from sqlalchemy import text


def test_postgres_connection():
    """Test PostgreSQL connection."""
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT 1"))
            print("✅ PostgreSQL connection successful")
            return True
    except Exception as e:
        print(f"❌ PostgreSQL connection failed: {e}")
        return False


def test_mongo_connection():
    """Test MongoDB connection."""
    try:
        # Ping MongoDB
        mongo_client.admin.command('ping')
        print("✅ MongoDB connection successful")
        return True
    except Exception as e:
        print(f"❌ MongoDB connection failed: {e}")
        return False


def test_redis_connection():
    """Test Redis connection."""
    try:
        if ping_redis():
            print("✅ Redis connection successful")
            return True
        else:
            print("❌ Redis ping failed")
            return False
    except Exception as e:
        print(f"❌ Redis connection failed: {e}")
        return False


def test_table_creation():
    """Test if tables can be created."""
    try:
        from app.db.base import Base
        Base.metadata.create_all(bind=engine)
        print("✅ Tables created successfully")
        return True
    except Exception as e:
        print(f"❌ Table creation failed: {e}")
        return False


if __name__ == "__main__":
    print("🔍 Validating database setup...\n")

    results = []
    results.append(test_postgres_connection())
    results.append(test_mongo_connection())
    results.append(test_redis_connection())
    results.append(test_table_creation())

    print(f"\n📊 Results: {sum(results)}/{len(results)} tests passed")

    if all(results):
        print("🎉 All validations passed!")
        sys.exit(0)
    else:
        print("💥 Some validations failed!")
        sys.exit(1)