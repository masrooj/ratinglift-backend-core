from pymongo import MongoClient
from pymongo.database import Database
from app.core.config import settings

# MongoDB client
client: MongoClient = MongoClient(settings.mongo_url)

# Database
db: Database = client.get_database()

# Collections
raw_reviews_collection = db.raw_reviews
ai_context_cache_collection = db.ai_context_cache
vector_embeddings_collection = db.vector_embeddings


def get_mongo_db() -> Database:
    """Dependency to get MongoDB database."""
    return db


def close_mongo_connection():
    """Close MongoDB connection."""
    client.close()