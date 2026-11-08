"""Admin-tunable service settings: validation, versioned saves, instances pending a restart."""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mongomock_motor import AsyncMongoMockClient

import service_settings as settings
from accounts import db, users
from accounts.routes import router as accounts_router
from settings_routes import router as settings_router

ORIGIN = {"origin": "http://testserver"}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def database():
    db.use(AsyncMongoMockClient()["settings_test"])
    run(db.init())
    yield
    db.use(None)


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(accounts_router)
    app.include_router(settings_router)
    return app


def client_for(app, role):
    run(users.create(f"{role}@example.com", role, "a password"))
    client = TestClient(app, headers=ORIGIN)
    client.post("/api/auth/login", json={"email": f"{role}@example.com", "password": "a password"}).raise_for_status()
    return client


def test_defaults_until_saved():
    values, version = run(settings.load())
    assert version == 0 and values == settings.defaults()


def test_validate_rejects_bad_values():
    with pytest.raises(settings.Invalid):
        settings.validate({"pause_s": 10})
    with pytest.raises(settings.Invalid):
        settings.validate({"asr_chunk": "0.08"})
    with pytest.raises(settings.Invalid):
        settings.validate({"nope": 1})
    with pytest.raises(settings.Invalid):
        settings.validate({"pause_s": True})
    assert settings.validate({"asr_idle_ttl_min": 10.0})["asr_idle_ttl_min"] == 10


def test_recording_settings_in_frames():
    rec = settings.recording_settings(settings.validate({"pause_s": 1.2, "min_switch_s": 0.4}))
    assert rec.pause_frames == 15 and rec.min_run_frames == 5
    assert rec.max_utterance_frames == 250 and rec.min_activity == 0.3


def test_versioned_save_detects_conflicts():
    values, version = run(settings.save({"pause_s": 1.5}, 0, "u"))
    assert version == 1 and values["pause_s"] == 1.5
    with pytest.raises(settings.Conflict):
        run(settings.save({"pause_s": 2.5}, 0, "u"))
    _, version = run(settings.save({"pause_s": 2.5}, 1, "u"))
    assert version == 2 and run(settings.load())[0]["pause_s"] == 2.5


def test_invalid_stored_values_fall_back_to_defaults():
    run(db.get().settings.insert_one({"_id": "service", "version": 3, "values": {"pause_s": 99, "gone": 1, "min_activity": 0.5}}))
    values, version = run(settings.load())
    assert version == 3 and values["pause_s"] == 2.0 and values["min_activity"] == 0.5 and "gone" not in values


def test_instance_reports_pending_restart():
    async def go():
        inst = settings.Instance()
        await inst.register(settings.defaults(), 0, lambda: 2)
        before = await settings.instances(settings.defaults())
        changed = settings.validate({"asr_chunk": "0.56", "pause_s": 1.0})
        after = await settings.instances(changed)
        await inst.stop()
        return before, after, await settings.instances(changed)

    before, after, gone = run(go())
    assert before[0]["pendingRestart"] == [] and before[0]["activeRecordings"] == 2
    assert after[0]["pendingRestart"] == ["Transcription chunk"]  # pause_s needs no restart
    assert gone == []


def test_settings_routes_are_admin_only(app):
    user = client_for(app, "user")
    assert user.get("/api/admin/settings").status_code == 403
    assert user.put("/api/admin/settings", json={"version": 0, "values": {}}).status_code == 403


def test_settings_routes(app):
    admin = client_for(app, "admin")
    state = admin.get("/api/admin/settings").json()
    assert state["version"] == 0 and {f["key"] for f in state["fields"]} == set(settings.FIELDS)

    r = admin.put("/api/admin/settings", json={"version": 0, "values": {**state["values"], "pause_s": 1.0, "diar_preset": "ultra_low"}})
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == 1 and body["values"]["pause_s"] == 1.0
    assert body["changed"] == {"next_recording": ["End a line after a pause of"], "restart": ["Speaker detection latency"]}

    assert admin.put("/api/admin/settings", json={"version": 0, "values": state["values"]}).status_code == 409
    r = admin.put("/api/admin/settings", json={"version": 1, "values": {"pause_s": 30}})
    assert r.status_code == 400 and "between" in r.json()["detail"]
