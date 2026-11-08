"""One-time links that let a user set their password.

There is no email: an admin creates a user (an `invite` link) or resets one (a
`reset` link) and passes the link on. The web app's URL is
`<origin>/#/set-password/<secret>`. Using a link consumes every open link for
that user and ends their sessions.
"""

from datetime import timedelta

from . import db, sessions, users
from .security import digest, new_secret, now

LIFETIME = {"invite": timedelta(days=7), "reset": timedelta(days=3)}


def path(secret: str) -> str:
    return f"/#/set-password/{secret}"


async def create(user_id: str, kind: str) -> str:
    """Issue a link, replacing any the user already had."""
    links = db.get().password_links
    await links.delete_many({"userId": user_id})
    secret = new_secret()
    at = now()
    await links.insert_one(
        {"_id": digest(secret), "userId": user_id, "kind": kind, "createdAt": at, "expiresAt": at + LIFETIME[kind]}
    )
    return secret


async def lookup(secret: str) -> tuple[dict, dict] | None:
    """The (link, user) for a live link, or None."""
    link = await db.get().password_links.find_one({"_id": digest(secret), "expiresAt": {"$gt": now()}})
    if not link:
        return None
    user = await users.active(link["userId"])
    return (link, user) if user else None


async def redeem(secret: str, password: str) -> dict | None:
    """Set the password and consume the link. Returns the user, or None if the link is dead."""
    found = await lookup(secret)
    if not found:
        return None
    _, user = found
    # Delete first: only one request can win a race for the same link.
    deleted = await db.get().password_links.delete_one({"_id": digest(secret)})
    if deleted.deleted_count != 1:
        return None
    await db.get().password_links.delete_many({"userId": user["_id"]})
    await users.set_password(user["_id"], password)
    await sessions.delete_for_user(user["_id"])
    return user
