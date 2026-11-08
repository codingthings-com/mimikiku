"""Mongo connection (MONGO_URI) and index setup.

Every instance of the server points at the same Mongo, so sessions, tokens and
transcripts work no matter which instance a request lands on.
"""

import logging
import os

from pymongo import AsyncMongoClient

log = logging.getLogger(__name__)

_db = None


def connect(uri: str | None = None):
    """Open the client for MONGO_URI; the database is the URI's path (default `mimikiku`)."""
    uri = uri or os.environ.get("MONGO_URI")
    if not uri:
        raise RuntimeError("MONGO_URI is not set: Mimikiku needs Mongo for accounts and transcripts")
    client = AsyncMongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=5000)
    use(client.get_default_database(default="mimikiku"))
    return _db


def use(database) -> None:
    """Install a database handle (tests pass an in-memory one)."""
    global _db
    _db = database


def get():
    if _db is None:
        raise RuntimeError("accounts database is not connected")
    return _db


async def init() -> None:
    """Check the connection and create indexes. Safe to run on every start."""
    db = get()
    await db.command("ping")
    await db.users.create_index("email", unique=True)
    await db.sessions.create_index("userId")
    await db.sessions.create_index("expiresAt", expireAfterSeconds=0)
    await db.tokens.create_index("id", unique=True)
    await db.tokens.create_index("userId")
    await db.password_links.create_index("userId")
    await db.password_links.create_index("expiresAt", expireAfterSeconds=0)
    await db.login_failures.create_index("email")
    await db.login_failures.create_index("expiresAt", expireAfterSeconds=0)
    await db.transcripts.create_index([("userId", 1), ("updatedAt", -1)])
    log.info("Accounts database ready")
