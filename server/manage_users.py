"""Command-line user management, for the first admin and for lockouts.

    python manage_users.py create-admin you@example.com
    python manage_users.py reset-link you@example.com
    python manage_users.py list

Run from server/ with MONGO_URI set (in Docker: `docker compose exec server
python manage_users.py ...`). Links are printed against PUBLIC_ORIGIN, or
http://localhost:7777 when it's unset.
"""

import argparse
import asyncio
import os
import sys

from dotenv import load_dotenv

from accounts import db, links, users
from accounts.auth import public_origins
from accounts.security import normalize_email


def _url(secret: str) -> str:
    origin = (public_origins() or ["http://localhost:7777"])[0]
    return origin + links.path(secret)


async def create_admin(email: str) -> None:
    user = await users.by_email(email)
    if user:
        await users.update(user["_id"], role="admin", disabled=False)
        print(f"{email} already exists; it is now an enabled admin.")
    else:
        user = await users.create(email, "admin")
        print(f"Created admin {email}.")
    kind = "reset" if user.get("passwordHash") else "invite"
    print(f"Set a password at: {_url(await links.create(user['_id'], kind))}")


async def reset_link(email: str) -> None:
    user = await users.by_email(email)
    if not user:
        sys.exit(f"No user {email}")
    kind = "reset" if user.get("passwordHash") else "invite"
    print(f"Password link for {email}: {_url(await links.create(user['_id'], kind))}")


async def list_users() -> None:
    for u in await users.list_all():
        flags = " (disabled)" if u.get("disabled") else ""
        print(f"{u['email']:40} {u['role']:6}{flags}")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("create-admin", "reset-link"):
        sub.add_parser(name).add_argument("email")
    sub.add_parser("list")
    args = parser.parse_args()

    db.connect()
    await db.init()
    if args.command == "list":
        await list_users()
        return
    email = normalize_email(args.email)
    if not email:
        sys.exit(f"Not an email address: {args.email}")
    await (create_admin if args.command == "create-admin" else reset_link)(email)


if __name__ == "__main__":
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
    asyncio.run(main())
