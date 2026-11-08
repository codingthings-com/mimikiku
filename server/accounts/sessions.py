"""Browser sessions: an opaque cookie whose SHA-256 is the `sessions` _id.

Sessions last SESSION_DAYS and slide forward when used in their second half.
Mongo's TTL index drops expired ones; lookups also check `expiresAt` because
the TTL monitor only runs once a minute.
"""

from datetime import timedelta

from . import db, users
from .security import digest, new_secret, now

COOKIE = "mimikiku_session"
SESSION_DAYS = 30


async def create(user_id: str) -> str:
    secret = new_secret()
    at = now()
    await db.get().sessions.insert_one(
        {
            "_id": digest(secret),
            "userId": user_id,
            "createdAt": at,
            "expiresAt": at + timedelta(days=SESSION_DAYS),
        }
    )
    await users.update(user_id, lastLoginAt=at)
    return secret


async def user_for(secret: str) -> tuple[dict, str] | None:
    """The active user for a session cookie, and the session id."""
    sessions = db.get().sessions
    at = now()
    session = await sessions.find_one({"_id": digest(secret), "expiresAt": {"$gt": at}})
    if not session:
        return None
    user = await users.active(session["userId"])
    if not user:
        return None
    full = timedelta(days=SESSION_DAYS)
    await sessions.update_one(
        {"_id": session["_id"], "expiresAt": {"$lt": at + full / 2}},
        {"$set": {"expiresAt": at + full}},
    )
    return user, session["_id"]


async def delete(secret: str) -> None:
    await db.get().sessions.delete_one({"_id": digest(secret)})


async def delete_for_user(user_id: str, keep: str | None = None) -> None:
    """End all of a user's sessions, except the one with id `keep`."""
    query = {"userId": user_id}
    if keep:
        query["_id"] = {"$ne": keep}
    await db.get().sessions.delete_many(query)
