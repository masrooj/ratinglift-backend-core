from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

# Load environment variables from .env.dev file
load_dotenv(dotenv_path=Path('.env.dev').resolve())

class Settings(BaseSettings):
    environment: str = "development"
    database_url: str = Field(default="postgresql://postgres:postgres@localhost:5432/ratinglift", alias="DATABASE_URL")
    mongo_url: str = Field(default="mongodb://localhost:27017/ratinglift", alias="MONGO_URL")
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    cors_origins: list[str] = ["*"]

    class Config:
        env_file = ".env.dev"
        populate_by_name = True  # Allow using field names or aliases
        extra = "ignore"  # Ignore extra environment variables

settings = Settings()
