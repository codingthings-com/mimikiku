"""Accounts, sessions, API tokens, transcripts and user admin, on an in-memory Mongo."""

import asyncio
import struct

import numpy as np
import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from mongomock_motor import AsyncMongoMockClient

from accounts import audio, db, transcripts, users
from accounts.auth import websocket_user
from accounts.routes import LOGIN_MAX_FAILURES, router
from accounts.sessions import COOKIE
from accounts.transcripts import RecordingWriter

ORIGIN = {"origin": "http://testserver"}
PASSWORD = "correct horse"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def database():
    db.use(AsyncMongoMockClient()["mimikiku_test"])
    run(db.init())
    yield
    db.use(None)


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(router)

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        principal, reason = await websocket_user(websocket)
        await websocket.accept()
        await websocket.send_json({"user": principal.user["email"] if principal else None, "reason": reason})
        await websocket.close()

    return app


def make_user(email="ann@example.com", role="user", password=PASSWORD):
    return run(users.create(email, role, password))


def signed_in(app, email="ann@example.com", role="user") -> TestClient:
    make_user(email, role)
    client = TestClient(app, headers=ORIGIN)
    r = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return client


# ---------------------------------------------------------------------------
# Sign-in and sessions
# ---------------------------------------------------------------------------


def test_login_sets_session_and_me(app):
    client = signed_in(app, "Ann@Example.com".lower())
    assert COOKIE in client.cookies
    me = client.get("/api/auth/me").json()
    assert me["email"] == "ann@example.com" and me["role"] == "user"


def test_login_is_case_insensitive_and_rejects_wrong_password(app):
    make_user()
    client = TestClient(app, headers=ORIGIN)
    assert client.post("/api/auth/login", json={"email": " ANN@example.com ", "password": PASSWORD}).status_code == 200
    assert client.post("/api/auth/login", json={"email": "ann@example.com", "password": "nope nope"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "bob@example.com", "password": PASSWORD}).status_code == 401


def test_login_rate_limited_after_repeated_failures(app):
    make_user()
    client = TestClient(app, headers=ORIGIN)
    for _ in range(LOGIN_MAX_FAILURES):
        assert client.post("/api/auth/login", json={"email": "ann@example.com", "password": "wrong!!!"}).status_code == 401
    r = client.post("/api/auth/login", json={"email": "ann@example.com", "password": PASSWORD})
    assert r.status_code == 429


def test_signed_out_requests_are_refused(app):
    client = TestClient(app)
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/transcripts").status_code == 401


def test_logout_ends_session(app):
    client = signed_in(app)
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/me").status_code == 401


def test_cookie_writes_need_same_origin(app):
    client = signed_in(app)
    cookies = {COOKIE: client.cookies[COOKIE]}
    bare = TestClient(app, cookies=cookies)
    assert bare.post("/api/transcripts", json={}).status_code == 403
    assert bare.post("/api/transcripts", json={}, headers={"origin": "https://evil.example"}).status_code == 403
    # Reads don't need it.
    assert bare.get("/api/transcripts").status_code == 200
    assert client.post("/api/transcripts", json={}).status_code == 200


def test_public_origin_is_allowed(app, monkeypatch):
    monkeypatch.setenv("PUBLIC_ORIGIN", "https://mimikiku.example.com")
    client = signed_in(app)
    r = client.post("/api/transcripts", json={}, headers={"origin": "https://mimikiku.example.com"})
    assert r.status_code == 200


def test_disabled_user_loses_session(app):
    client = signed_in(app)
    user = run(users.by_email("ann@example.com"))
    run(users.update(user["_id"], disabled=True))
    assert client.get("/api/auth/me").status_code == 401


def test_change_password_ends_other_sessions(app):
    client = signed_in(app)
    other = TestClient(app, headers=ORIGIN)
    other.post("/api/auth/login", json={"email": "ann@example.com", "password": PASSWORD})

    assert client.post("/api/auth/password", json={"current": "wrong!!!", "new": "new password"}).status_code == 400
    assert client.post("/api/auth/password", json={"current": PASSWORD, "new": "short"}).status_code == 400
    assert client.post("/api/auth/password", json={"current": PASSWORD, "new": "new password"}).status_code == 200

    assert client.get("/api/auth/me").status_code == 200
    assert other.get("/api/auth/me").status_code == 401
    fresh = TestClient(app, headers=ORIGIN)
    assert fresh.post("/api/auth/login", json={"email": "ann@example.com", "password": "new password"}).status_code == 200


# ---------------------------------------------------------------------------
# API tokens
# ---------------------------------------------------------------------------


def test_api_token_reaches_transcripts_only(app):
    client = signed_in(app)
    created = client.post("/api/tokens", json={"name": "laptop"}).json()
    token = created["token"]
    assert token.startswith("mk_") and created["hint"] == token[-4:]

    bearer = TestClient(app, headers={"authorization": f"Bearer {token}"})
    assert bearer.post("/api/transcripts", json={"title": "via token"}).status_code == 200
    assert bearer.get("/api/transcripts").json()[0]["title"] == "via token"
    assert bearer.get("/api/tokens").status_code == 403
    assert bearer.post("/api/auth/password", json={"current": PASSWORD, "new": "x" * 10}).status_code == 403

    listed = client.get("/api/tokens").json()
    assert [t["name"] for t in listed] == ["laptop"] and "token" not in listed[0]
    assert listed[0]["lastUsedAt"] is not None

    assert client.delete(f"/api/tokens/{created['id']}").status_code == 200
    assert bearer.get("/api/transcripts").status_code == 401


def test_tokens_are_per_user(app):
    ann = signed_in(app)
    token_id = ann.post("/api/tokens", json={"name": "a"}).json()["id"]
    bob = signed_in(app, "bob@example.com")
    assert bob.delete(f"/api/tokens/{token_id}").status_code == 404
    assert bob.get("/api/tokens").json() == []


# ---------------------------------------------------------------------------
# Transcripts
# ---------------------------------------------------------------------------


def seed_transcript(user_email="ann@example.com", lines=("Hello there", "General Kenobi")):
    user = run(users.by_email(user_email))

    async def go():
        writer = RecordingWriter(user["_id"])
        await writer.open()
        segs = writer.stamp([{"speaker": i % 2, "text": t, "start": i, "end": i + 1} for i, t in enumerate(lines)])
        await writer.append(segs)
        return writer

    return run(go())


def test_transcript_crud(app):
    client = signed_in(app)
    writer = seed_transcript()
    tid = writer.transcript_id
    rid = writer.recording["id"]

    summary = client.get("/api/transcripts").json()
    assert len(summary) == 1
    assert summary[0]["segmentCount"] == 2 and summary[0]["recordingCount"] == 1
    assert summary[0]["preview"] == "Hello there"

    doc = client.get(f"/api/transcripts/{tid}").json()
    assert [s["id"] for s in doc["segments"]] == [f"{rid}:1", f"{rid}:2"]
    assert doc["recordings"][0]["id"] == rid and doc["recordings"][0]["audio"] is False

    r = client.patch(f"/api/transcripts/{tid}", json={"title": " Standup ", "names": {f"{rid}:0": "Obi-Wan"}})
    assert r.status_code == 200
    assert r.json()["title"] == "Standup" and r.json()["names"] == {f"{rid}:0": "Obi-Wan"}
    r = client.patch(f"/api/transcripts/{tid}", json={"names": {f"{rid}:0": ""}})
    assert r.json()["names"] == {}
    assert client.patch(f"/api/transcripts/{tid}", json={"names": {"bad.key": "x"}}).status_code == 400

    assert client.patch(f"/api/transcripts/{tid}/segments/{rid}:2", json={"text": "General Grievous"}).status_code == 200
    assert client.delete(f"/api/transcripts/{tid}/segments/{rid}:1").status_code == 200
    assert client.delete(f"/api/transcripts/{tid}/segments/{rid}:1").status_code == 404
    doc = client.get(f"/api/transcripts/{tid}").json()
    assert [s["text"] for s in doc["segments"]] == ["General Grievous"]
    assert client.get("/api/transcripts").json()[0]["segmentCount"] == 1

    assert client.delete(f"/api/transcripts/{tid}").status_code == 200
    assert client.get(f"/api/transcripts/{tid}").status_code == 404
    assert client.get("/api/transcripts").json() == []


def test_create_empty_transcript(app):
    client = signed_in(app)
    doc = client.post("/api/transcripts", json={"title": "Notes"}).json()
    assert doc["title"] == "Notes" and doc["segments"] == [] and doc["recordings"] == []


def test_transcripts_are_private(app):
    ann = signed_in(app)
    tid = seed_transcript().transcript_id
    bob = signed_in(app, "bob@example.com")
    assert bob.get("/api/transcripts").json() == []
    assert bob.get(f"/api/transcripts/{tid}").status_code == 404
    assert bob.patch(f"/api/transcripts/{tid}", json={"title": "mine"}).status_code == 404
    assert bob.delete(f"/api/transcripts/{tid}").status_code == 404
    assert ann.get(f"/api/transcripts/{tid}").status_code == 200


def test_transcript_search(app):
    client = signed_in(app)
    seed_transcript(lines=("Budget review",))
    seed_transcript(lines=("Lunch plans (tacos?)",))
    assert [t["preview"] for t in client.get("/api/transcripts", params={"q": "tacos?"}).json()] == [
        "Lunch plans (tacos?)"
    ]
    assert len(client.get("/api/transcripts", params={"q": "BUDGET"}).json()) == 1
    assert len(client.get("/api/transcripts").json()) == 2


# ---------------------------------------------------------------------------
# Recording writer
# ---------------------------------------------------------------------------


def test_writer_saves_nothing_without_segments():
    user = make_user()
    RecordingWriter(user["_id"])
    assert run(transcripts.list_for(user["_id"])) == []


def test_writer_creates_then_appends_recordings():
    user = make_user()

    async def go():
        first = RecordingWriter(user["_id"])
        event = await first.open()
        assert event["type"] == "transcript" and event["id"] == first.transcript_id
        assert isinstance(event["recording"]["startedAt"], str)
        assert await first.open() is None  # once only
        await first.append(first.stamp([{"speaker": 0, "text": "one", "start": 0, "end": 1, "language": "en"}]))

        second = RecordingWriter(user["_id"], first.transcript_id)
        await second.open()
        await second.append(second.stamp([{"speaker": 0, "text": "two", "start": 0, "end": 1}]))
        return first.transcript_id

    tid = run(go())
    doc = run(transcripts.get(user["_id"], tid))
    assert len(doc["recordings"]) == 2 and doc["recordingCount"] == 2
    assert [s["text"] for s in doc["segments"]] == ["one", "two"]
    assert "language" not in doc["segments"][0]
    assert doc["segments"][0]["recording"] != doc["segments"][1]["recording"]


def test_writer_stops_when_transcript_deleted():
    user = make_user()

    async def go():
        writer = RecordingWriter(user["_id"])
        await writer.open()
        assert writer.saved
        await transcripts.delete(user["_id"], writer.transcript_id)
        await writer.append(writer.stamp([{"speaker": 0, "text": "late", "start": 0, "end": 1}]))
        return writer

    writer = run(go())
    assert not writer.saved
    assert run(transcripts.list_for(user["_id"])) == []


def test_writer_wont_append_to_someone_elses_transcript():
    ann = make_user()
    bob = make_user("bob@example.com")
    tid = run(transcripts.create(ann["_id"]))["_id"]
    writer = RecordingWriter(bob["_id"], tid)
    assert run(writer.open()) is None
    assert not writer.saved


# ---------------------------------------------------------------------------
# WebSocket auth
# ---------------------------------------------------------------------------


def ws_result(client, **headers):
    with client.websocket_connect("/ws", headers=headers) as ws:
        return ws.receive_json()


def test_websocket_auth(app):
    client = signed_in(app)
    assert ws_result(client, **ORIGIN)["user"] == "ann@example.com"
    refused = ws_result(client, origin="https://evil.example")
    assert refused["user"] is None and "Cross-origin" in refused["reason"]
    assert ws_result(TestClient(app))["reason"] == "Sign in required"

    token = client.post("/api/tokens", json={"name": "tui"}).json()["token"]
    assert ws_result(TestClient(app), authorization=f"Bearer {token}")["user"] == "ann@example.com"
    assert ws_result(TestClient(app), authorization="Bearer mk_wrong")["user"] is None


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------


def test_admin_routes_need_admin(app):
    client = signed_in(app)
    assert client.get("/api/admin/users").status_code == 403
    assert client.post("/api/admin/users", json={"email": "x@example.com"}).status_code == 403


def test_invite_flow(app):
    admin = signed_in(app, "root@example.com", "admin")
    r = admin.post("/api/admin/users", json={"email": "New@Example.com"})
    assert r.status_code == 200
    assert r.json()["user"]["email"] == "new@example.com" and not r.json()["user"]["hasPassword"]
    secret = r.json()["link"].rsplit("/", 1)[1]
    assert admin.post("/api/admin/users", json={"email": "new@example.com"}).status_code == 409

    guest = TestClient(app, headers=ORIGIN)
    assert guest.get(f"/api/auth/links/{secret}").json() == {"email": "new@example.com", "kind": "invite"}
    assert guest.post(f"/api/auth/links/{secret}", json={"password": "short"}).status_code == 400
    assert guest.post(f"/api/auth/links/{secret}", json={"password": "a fine password"}).status_code == 200
    assert guest.get("/api/auth/me").json()["email"] == "new@example.com"
    assert guest.post(f"/api/auth/links/{secret}", json={"password": "again password"}).status_code == 404
    assert guest.get(f"/api/auth/links/{secret}").status_code == 404


def test_admin_sets_password_and_role(app):
    admin = signed_in(app, "root@example.com", "admin")
    user = admin.post("/api/admin/users", json={"email": "bob@example.com", "password": "bobs password"}).json()["user"]
    assert user["hasPassword"] and "link" not in user

    bob = TestClient(app, headers=ORIGIN)
    assert bob.post("/api/auth/login", json={"email": "bob@example.com", "password": "bobs password"}).status_code == 200

    r = admin.patch(f"/api/admin/users/{user['id']}", json={"password": "reset by admin", "role": "admin"})
    assert r.status_code == 200 and r.json()["role"] == "admin"
    assert bob.get("/api/auth/me").status_code == 401  # password change ends sessions
    assert bob.post("/api/auth/login", json={"email": "bob@example.com", "password": "reset by admin"}).status_code == 200
    assert bob.get("/api/admin/users").status_code == 200


def test_reset_link_replaces_older_links(app):
    admin = signed_in(app, "root@example.com", "admin")
    bob = make_user("bob@example.com")
    first = admin.post(f"/api/admin/users/{bob['_id']}/reset-link").json()["link"].rsplit("/", 1)[1]
    second = admin.post(f"/api/admin/users/{bob['_id']}/reset-link").json()["link"].rsplit("/", 1)[1]
    guest = TestClient(app, headers=ORIGIN)
    assert guest.get(f"/api/auth/links/{first}").status_code == 404
    assert guest.get(f"/api/auth/links/{second}").json()["kind"] == "reset"


def test_admin_disables_user(app):
    admin = signed_in(app, "root@example.com", "admin")
    bob = signed_in(app, "bob@example.com")
    bob_id = run(users.by_email("bob@example.com"))["_id"]
    token = bob.post("/api/tokens", json={"name": "t"}).json()["token"]

    assert admin.patch(f"/api/admin/users/{bob_id}", json={"disabled": True}).json()["disabled"] is True
    assert bob.get("/api/auth/me").status_code == 401
    assert TestClient(app, headers={"authorization": f"Bearer {token}"}).get("/api/transcripts").status_code == 401
    retry = TestClient(app, headers=ORIGIN)
    assert retry.post("/api/auth/login", json={"email": "bob@example.com", "password": PASSWORD}).status_code == 401

    admin.patch(f"/api/admin/users/{bob_id}", json={"disabled": False})
    assert retry.post("/api/auth/login", json={"email": "bob@example.com", "password": PASSWORD}).status_code == 200


def test_admin_cannot_lock_themselves_out(app):
    admin = signed_in(app, "root@example.com", "admin")
    me = admin.get("/api/auth/me").json()["id"]
    assert admin.patch(f"/api/admin/users/{me}", json={"role": "user"}).status_code == 400
    assert admin.patch(f"/api/admin/users/{me}", json={"disabled": True}).status_code == 400
    assert admin.delete(f"/api/admin/users/{me}").status_code == 400
    # Changing their own password keeps their session.
    assert admin.patch(f"/api/admin/users/{me}", json={"password": "my new password"}).status_code == 200
    assert admin.get("/api/auth/me").status_code == 200


def test_admin_deletes_user_and_their_data(app, monkeypatch):
    deleted = []

    async def fake_delete_for_user(user_id):
        deleted.append(user_id)

    monkeypatch.setattr(audio, "delete_for_user", fake_delete_for_user)
    admin = signed_in(app, "root@example.com", "admin")
    bob = signed_in(app, "bob@example.com")
    bob_id = run(users.by_email("bob@example.com"))["_id"]
    seed_transcript("bob@example.com")

    assert admin.delete(f"/api/admin/users/{bob_id}").status_code == 200
    assert deleted == [bob_id]
    assert run(transcripts.list_for(bob_id)) == []
    assert bob.get("/api/auth/me").status_code == 401
    assert [u["email"] for u in admin.get("/api/admin/users").json()] == ["root@example.com"]


def test_admins_cannot_read_other_transcripts(app):
    signed_in(app)
    tid = seed_transcript().transcript_id
    admin = signed_in(app, "root@example.com", "admin")
    assert admin.get(f"/api/transcripts/{tid}").status_code == 404
    assert admin.get("/api/transcripts").json() == []


# ---------------------------------------------------------------------------
# Audio encoding
# ---------------------------------------------------------------------------


def test_pcm16_and_wav_header():
    pcm = audio.to_pcm16(np.array([[0.0, 1.0, -1.0, 2.0]], dtype=np.float32))
    assert struct.unpack("<4h", pcm) == (0, 32767, -32767, 32767)

    header = audio.wav_header(len(pcm), 16000)
    assert len(header) == 44 and header[:4] == b"RIFF" and header[8:16] == b"WAVEfmt "
    assert struct.unpack("<I", header[4:8])[0] == 36 + len(pcm)
    assert struct.unpack("<I", header[24:28])[0] == 16000
    assert struct.unpack("<I", header[40:44])[0] == len(pcm)


# ---------------------------------------------------------------------------
# First admin from .env
# ---------------------------------------------------------------------------


def test_seed_admin_only_on_empty_database(app):
    assert run(users.seed_admin(None, None)) is None
    assert run(users.seed_admin("not-an-email", PASSWORD)) is None
    assert run(users.seed_admin("root@example.com", "short")) is None
    assert run(users.count()) == 0

    seeded = run(users.seed_admin(" Root@Example.com ", PASSWORD))
    assert seeded["email"] == "root@example.com" and seeded["role"] == "admin"
    client = TestClient(app, headers=ORIGIN)
    assert client.post("/api/auth/login", json={"email": "root@example.com", "password": PASSWORD}).status_code == 200

    # Later starts (or edited .env values) leave accounts alone.
    assert run(users.seed_admin("other@example.com", "another password")) is None
    assert run(users.seed_admin("root@example.com", "another password")) is None
    assert run(users.count()) == 1
