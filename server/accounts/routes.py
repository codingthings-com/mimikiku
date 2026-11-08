"""HTTP API for accounts, API tokens, transcripts and user administration.

    /api/auth/*         sign in/out, current user, own password, password links
    /api/tokens         own API tokens (web session only)
    /api/transcripts    own transcripts (session or API token)
    /api/admin/users    user management (admins, web session only)
"""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from telemetry import instruments

from . import audio, db, links, sessions, tokens, transcripts, users
from .auth import Principal, admin, clear_session_cookie, current, session_user, set_session_cookie
from .security import normalize_email, now, password_problem, verify_password
from .sessions import COOKIE

router = APIRouter(prefix="/api")

LOGIN_WINDOW = timedelta(minutes=15)
LOGIN_MAX_FAILURES = 10


def _event(action: str, outcome: str = "ok") -> None:
    instruments.auth_events.add(1, {"action": action, "outcome": outcome})


def _me(user: dict) -> dict:
    return {"id": user["_id"], "email": user["email"], "role": user["role"]}


def _check_password(password: str) -> None:
    if problem := password_problem(password):
        raise HTTPException(400, problem)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class Login(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=256)


class PasswordChange(BaseModel):
    current: str = Field(max_length=256)
    new: str = Field(max_length=256)


class NewPassword(BaseModel):
    password: str = Field(max_length=256)


@router.post("/auth/login")
async def login(body: Login, request: Request, response: Response):
    email = normalize_email(body.email) or body.email.strip().lower()
    failures = db.get().login_failures
    recent = await failures.count_documents({"email": email, "expiresAt": {"$gt": now()}})
    if recent >= LOGIN_MAX_FAILURES:
        _event("login", "limited")
        raise HTTPException(429, "Too many failed attempts. Try again in a few minutes.")

    user = await users.by_email(email)
    ok = verify_password(user.get("passwordHash") if user else None, body.password)
    if not ok or user.get("disabled"):
        await failures.insert_one({"email": email, "expiresAt": now() + LOGIN_WINDOW})
        _event("login", "failed")
        raise HTTPException(401, "Wrong email or password")

    await failures.delete_many({"email": email})
    set_session_cookie(request, response, await sessions.create(user["_id"]))
    _event("login")
    return _me(user)


@router.post("/auth/logout")
async def logout(request: Request, response: Response):
    if secret := request.cookies.get(COOKIE):
        await sessions.delete(secret)
    clear_session_cookie(response)
    _event("logout")
    return {"ok": True}


@router.get("/auth/me")
async def me(principal: Principal = Depends(current)):
    return _me(principal.user)


@router.post("/auth/password")
async def change_password(body: PasswordChange, principal: Principal = Depends(session_user)):
    if not verify_password(principal.user.get("passwordHash"), body.current):
        _event("password_change", "failed")
        raise HTTPException(400, "Current password is wrong")
    _check_password(body.new)
    await users.set_password(principal.id, body.new)
    await sessions.delete_for_user(principal.id, keep=principal.session_id)
    _event("password_change")
    return {"ok": True}


@router.get("/auth/links/{secret}")
async def get_link(secret: str):
    found = await links.lookup(secret)
    if not found:
        raise HTTPException(404, "This link has expired or was already used")
    link, user = found
    return {"email": user["email"], "kind": link["kind"]}


@router.post("/auth/links/{secret}")
async def use_link(secret: str, body: NewPassword, request: Request, response: Response):
    _check_password(body.password)
    user = await links.redeem(secret, body.password)
    if not user:
        _event("password_link", "invalid")
        raise HTTPException(404, "This link has expired or was already used")
    set_session_cookie(request, response, await sessions.create(user["_id"]))
    _event("password_link")
    return _me(user)


# ---------------------------------------------------------------------------
# API tokens
# ---------------------------------------------------------------------------


class NewToken(BaseModel):
    name: str = Field(min_length=1, max_length=60)


@router.get("/tokens")
async def list_tokens(principal: Principal = Depends(session_user)):
    return [tokens.public(t) for t in await tokens.list_for(principal.id)]


@router.post("/tokens")
async def create_token(body: NewToken, principal: Principal = Depends(session_user)):
    secret, token = await tokens.create(principal.id, body.name.strip())
    _event("token_create")
    return {**tokens.public(token), "token": secret}


@router.delete("/tokens/{token_id}")
async def revoke_token(token_id: str, principal: Principal = Depends(session_user)):
    if not await tokens.revoke(principal.id, token_id):
        raise HTTPException(404, "Token not found")
    _event("token_revoke")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Transcripts
# ---------------------------------------------------------------------------


class NewTranscript(BaseModel):
    title: str = Field("", max_length=transcripts.MAX_TITLE)


class TranscriptPatch(BaseModel):
    title: str | None = Field(None, max_length=transcripts.MAX_TITLE)
    names: dict[str, str] | None = None


class SegmentPatch(BaseModel):
    text: str = Field(min_length=1, max_length=transcripts.MAX_TEXT)


async def _owned(principal: Principal, transcript_id: str) -> dict:
    doc = await transcripts.get(principal.id, transcript_id)
    if not doc:
        raise HTTPException(404, "Transcript not found")
    return doc


@router.get("/transcripts")
async def list_transcripts(
    q: str = Query("", max_length=200),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    principal: Principal = Depends(current),
):
    docs = await transcripts.list_for(principal.id, q, limit, offset)
    return [transcripts.summary(d) for d in docs]


@router.post("/transcripts")
async def create_transcript(body: NewTranscript, principal: Principal = Depends(current)):
    return transcripts.public(await transcripts.create(principal.id, body.title.strip()))


@router.get("/transcripts/{transcript_id}")
async def get_transcript(transcript_id: str, principal: Principal = Depends(current)):
    return transcripts.public(await _owned(principal, transcript_id))


@router.patch("/transcripts/{transcript_id}")
async def update_transcript(transcript_id: str, body: TranscriptPatch, principal: Principal = Depends(current)):
    for key, name in (body.names or {}).items():
        if not transcripts.NAME_KEY.match(key) or len(name) > transcripts.MAX_NAME:
            raise HTTPException(400, f"Invalid speaker name entry: {key}")
    if not await transcripts.update(principal.id, transcript_id, body.title, body.names):
        raise HTTPException(404, "Transcript not found")
    return transcripts.public(await _owned(principal, transcript_id))


@router.delete("/transcripts/{transcript_id}")
async def delete_transcript(transcript_id: str, principal: Principal = Depends(current)):
    if not await transcripts.delete(principal.id, transcript_id):
        raise HTTPException(404, "Transcript not found")
    return {"ok": True}


@router.patch("/transcripts/{transcript_id}/segments/{segment_id}")
async def edit_segment(
    transcript_id: str, segment_id: str, body: SegmentPatch, principal: Principal = Depends(current)
):
    if not body.text.strip():
        raise HTTPException(400, "Text can't be empty; delete the line instead")
    if not await transcripts.edit_segment(principal.id, transcript_id, segment_id, body.text):
        raise HTTPException(404, "Line not found")
    return {"ok": True}


@router.delete("/transcripts/{transcript_id}/segments/{segment_id}")
async def delete_segment(transcript_id: str, segment_id: str, principal: Principal = Depends(current)):
    if not await transcripts.delete_segment(principal.id, transcript_id, segment_id):
        raise HTTPException(404, "Line not found")
    return {"ok": True}


@router.get("/transcripts/{transcript_id}/recordings/{recording_id}/audio")
async def recording_audio(transcript_id: str, recording_id: str, principal: Principal = Depends(current)):
    file_id = await transcripts.audio_id(principal.id, transcript_id, recording_id)
    if file_id is None:
        raise HTTPException(404, "No audio for this recording")
    length, body = await audio.stream_wav(file_id)
    return StreamingResponse(
        body,
        media_type="audio/wav",
        headers={
            "Content-Length": str(length),
            "Content-Disposition": f'attachment; filename="mimikiku-{recording_id}.wav"',
        },
    )


# ---------------------------------------------------------------------------
# Admin: users
# ---------------------------------------------------------------------------


class NewUser(BaseModel):
    email: str = Field(max_length=254)
    role: str = Field("user", pattern="^(user|admin)$")
    password: str | None = Field(None, max_length=256)


class UserPatch(BaseModel):
    role: str | None = Field(None, pattern="^(user|admin)$")
    disabled: bool | None = None
    password: str | None = Field(None, max_length=256)


async def _target(user_id: str) -> dict:
    user = await users.by_id(user_id)
    if not user:
        raise HTTPException(404, "User not found")
    return user


@router.get("/admin/users", dependencies=[Depends(admin)])
async def admin_list_users():
    return [users.public(u) for u in await users.list_all()]


@router.post("/admin/users", dependencies=[Depends(admin)])
async def admin_create_user(body: NewUser):
    email = normalize_email(body.email)
    if not email:
        raise HTTPException(400, "Enter a valid email address")
    if body.password:
        _check_password(body.password)
    try:
        user = await users.create(email, body.role, body.password or None)
    except users.EmailTaken:
        raise HTTPException(409, "A user with that email already exists") from None
    _event("admin_user_create")
    result = {"user": users.public(user)}
    if not body.password:
        result["link"] = links.path(await links.create(user["_id"], "invite"))
    return result


@router.patch("/admin/users/{user_id}")
async def admin_update_user(user_id: str, body: UserPatch, principal: Principal = Depends(admin)):
    user = await _target(user_id)
    if user_id == principal.id and (body.role not in (None, user["role"]) or body.disabled):
        raise HTTPException(400, "You can't demote or disable yourself")
    fields = {}
    if body.role is not None:
        fields["role"] = body.role
    if body.disabled is not None:
        fields["disabled"] = body.disabled
    if fields:
        await users.update(user_id, **fields)
    if body.password:
        _check_password(body.password)
        await users.set_password(user_id, body.password)
    if body.disabled or body.password:
        await sessions.delete_for_user(user_id, keep=principal.session_id if user_id == principal.id else None)
    _event("admin_user_update")
    return users.public(await _target(user_id))


@router.post("/admin/users/{user_id}/reset-link", dependencies=[Depends(admin)])
async def admin_reset_link(user_id: str):
    user = await _target(user_id)
    kind = "reset" if user.get("passwordHash") else "invite"
    _event("admin_password_link")
    return {"link": links.path(await links.create(user_id, kind))}


@router.delete("/admin/users/{user_id}")
async def admin_delete_user(user_id: str, principal: Principal = Depends(admin)):
    await _target(user_id)
    if user_id == principal.id:
        raise HTTPException(400, "You can't delete yourself")
    await users.delete(user_id)
    _event("admin_user_delete")
    return {"ok": True}
