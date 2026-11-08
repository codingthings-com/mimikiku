"""Personal API tokens for clients that can't hold a cookie (mimikiku-capture).

A token is shown once at creation; `tokens` stores its SHA-256 as _id, plus a
public `id` used to list and revoke it. Tokens can stream to /listen and use the
transcript API, never account or admin routes.
"""

from . import db, users
from .security import digest, new_api_token, new_id, now


def public(token: dict) -> dict:
    return {
        "id": token["id"],
        "name": token["name"],
        "hint": token["hint"],
        "createdAt": token["createdAt"],
        "lastUsedAt": token.get("lastUsedAt"),
    }


async def create(user_id: str, name: str) -> tuple[str, dict]:
    secret = new_api_token()
    token = {
        "_id": digest(secret),
        "id": new_id(),
        "userId": user_id,
        "name": name,
        "hint": secret[-4:],
        "createdAt": now(),
        "lastUsedAt": None,
    }
    await db.get().tokens.insert_one(token)
    return secret, token


async def list_for(user_id: str) -> list[dict]:
    return await db.get().tokens.find({"userId": user_id}).sort("createdAt", -1).to_list(None)


async def revoke(user_id: str, token_id: str) -> bool:
    result = await db.get().tokens.delete_one({"userId": user_id, "id": token_id})
    return result.deleted_count == 1


async def user_for(secret: str) -> dict | None:
    token = await db.get().tokens.find_one_and_update(
        {"_id": digest(secret)}, {"$set": {"lastUsedAt": now()}}
    )
    if not token:
        return None
    return await users.active(token["userId"])
