"""Users: `{_id, email, passwordHash, role, disabled, createdAt, lastLoginAt}`.

`passwordHash` is None until an invited user sets a password. Roles are
`user` and `admin`; admins manage accounts, settings and monitoring.
"""

import logging

from pymongo.errors import DuplicateKeyError

from . import db
from .security import hash_password, new_id, normalize_email, now, password_problem

log = logging.getLogger(__name__)

ROLES = ("user", "admin")


class EmailTaken(Exception):
    pass


def public(user: dict) -> dict:
    return {
        "id": user["_id"],
        "email": user["email"],
        "role": user["role"],
        "disabled": bool(user.get("disabled")),
        "hasPassword": user.get("passwordHash") is not None,
        "createdAt": user.get("createdAt"),
        "lastLoginAt": user.get("lastLoginAt"),
    }


async def create(email: str, role: str = "user", password: str | None = None) -> dict:
    user = {
        "_id": new_id(),
        "email": email,
        "passwordHash": hash_password(password) if password else None,
        "role": role,
        "disabled": False,
        "createdAt": now(),
        "lastLoginAt": None,
    }
    try:
        await db.get().users.insert_one(user)
    except DuplicateKeyError:
        raise EmailTaken(email) from None
    return user


EXAMPLE_ADMIN_PASSWORD = "change-me-now"  # the placeholder in .env.example


async def seed_admin(email: str | None, password: str | None) -> dict | None:
    """Create the first admin from MIMIKIKU_ADMIN_EMAIL / MIMIKIKU_ADMIN_PASSWORD.

    Only when there are no users at all: later edits to .env never touch
    existing accounts (change the password in the web app instead).
    """
    if not email or not password or await count() > 0:
        return None
    normalized = normalize_email(email)
    if not normalized:
        log.error(f"MIMIKIKU_ADMIN_EMAIL is not an email address: {email!r}; no admin created")
        return None
    if problem := password_problem(password):
        log.error(f"MIMIKIKU_ADMIN_PASSWORD rejected ({problem}); no admin created")
        return None
    try:
        user = await create(normalized, "admin", password)
    except EmailTaken:
        return None  # another instance seeded it first
    log.info(f"Created the first admin, {normalized}, from MIMIKIKU_ADMIN_EMAIL")
    if password == EXAMPLE_ADMIN_PASSWORD:
        log.warning("The first admin still has the example password from .env.example: change it in the web app (Account)")
    return user


async def by_id(user_id: str) -> dict | None:
    return await db.get().users.find_one({"_id": user_id})


async def by_email(email: str) -> dict | None:
    return await db.get().users.find_one({"email": email})


async def active(user_id: str) -> dict | None:
    return await db.get().users.find_one({"_id": user_id, "disabled": {"$ne": True}})


async def list_all() -> list[dict]:
    return await db.get().users.find().sort("createdAt", 1).to_list(None)


async def count() -> int:
    return await db.get().users.count_documents({})


async def update(user_id: str, **fields) -> bool:
    result = await db.get().users.update_one({"_id": user_id}, {"$set": fields})
    return result.matched_count == 1


async def set_password(user_id: str, password: str) -> None:
    await update(user_id, passwordHash=hash_password(password))


async def delete(user_id: str) -> None:
    """Remove the user and everything they own."""
    from . import audio, transcripts  # avoid an import cycle

    database = db.get()
    await audio.delete_for_user(user_id)
    await transcripts.delete_for_user(user_id)
    for coll in (database.sessions, database.tokens, database.password_links):
        await coll.delete_many({"userId": user_id})
    await database.users.delete_one({"_id": user_id})
