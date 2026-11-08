"""Who is calling: a session cookie (the web app) or a Bearer API token (mimikiku-capture).

Cookie-authenticated requests that change something, and every cookie-
authenticated WebSocket, must come from our own origin. Browsers attach cookies
to cross-site requests and WebSocket handshakes, so without this check another
site could act as the signed-in user. Bearer tokens are never sent
automatically, so they need no origin check.
"""

import os
from dataclasses import dataclass
from urllib.parse import urlparse

from fastapi import Depends, HTTPException, Request, Response, WebSocket

from . import sessions, tokens
from .sessions import COOKIE, SESSION_DAYS

SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


@dataclass
class Principal:
    user: dict
    via: str  # "session" | "token"
    session_id: str | None = None

    @property
    def id(self) -> str:
        return self.user["_id"]

    @property
    def is_admin(self) -> bool:
        return self.user["role"] == "admin"


def public_origins() -> list[str]:
    """PUBLIC_ORIGIN: comma-separated origins the app is served from, beyond the request's own host."""
    return [o.strip().rstrip("/") for o in os.environ.get("PUBLIC_ORIGIN", "").split(",") if o.strip()]


def same_origin(headers) -> bool:
    origin = headers.get("origin")
    if not origin:
        return False
    hosts = {headers.get("host"), headers.get("x-forwarded-host")}
    hosts |= {urlparse(o).netloc for o in public_origins()}
    hosts.discard(None)
    return urlparse(origin).netloc in hosts


def bearer(headers) -> str | None:
    value = headers.get("authorization", "")
    scheme, _, secret = value.partition(" ")
    return secret.strip() if scheme.lower() == "bearer" and secret.strip() else None


async def identify(headers, cookies) -> Principal | None:
    if secret := bearer(headers):
        user = await tokens.user_for(secret)
        return Principal(user, "token") if user else None
    if secret := cookies.get(COOKIE):
        found = await sessions.user_for(secret)
        if found:
            user, session_id = found
            return Principal(user, "session", session_id)
    return None


async def current(request: Request) -> Principal:
    """Any signed-in caller: session cookie or API token."""
    principal = await identify(request.headers, request.cookies)
    if not principal:
        raise HTTPException(401, "Sign in required")
    if principal.via == "session" and request.method not in SAFE_METHODS and not same_origin(request.headers):
        raise HTTPException(403, "Cross-origin request refused")
    return principal


async def session_user(principal: Principal = Depends(current)) -> Principal:
    """Signed in through the web app; API tokens can't manage accounts."""
    if principal.via != "session":
        raise HTTPException(403, "Not available to API tokens")
    return principal


async def admin(principal: Principal = Depends(session_user)) -> Principal:
    if not principal.is_admin:
        raise HTTPException(403, "Admins only")
    return principal


async def websocket_user(websocket: WebSocket) -> tuple[Principal | None, str]:
    """The caller of a WebSocket handshake, or (None, reason)."""
    principal = await identify(websocket.headers, websocket.cookies)
    if not principal:
        return None, "Sign in required"
    if principal.via == "session" and not same_origin(websocket.headers):
        return None, "Cross-origin connection refused"
    return principal, ""


def is_https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


def set_session_cookie(request: Request, response: Response, secret: str) -> None:
    response.set_cookie(
        COOKIE,
        secret,
        max_age=SESSION_DAYS * 86400,
        httponly=True,
        samesite="lax",
        secure=is_https(request),
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE, path="/")
