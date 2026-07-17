"""
app/db/mongo.py — async Motor client for MongoDB.

Lazily initialised; call close_mongo_client() in app shutdown.
Usage: db = get_mongo_db()  →  db["generations"].insert_one(...)
"""
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.config import settings

_client: AsyncIOMotorClient | None = None


def get_mongo_client() -> AsyncIOMotorClient:
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(settings.MONGODB_URI)
    return _client


def get_mongo_db() -> AsyncIOMotorDatabase:
    return get_mongo_client()[settings.MONGODB_DB_NAME]


async def close_mongo_client() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None
