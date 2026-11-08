"""Admin API for service settings (see service_settings.py)."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import service_settings as settings
from accounts.auth import Principal, admin
from telemetry import instruments

router = APIRouter(prefix="/api/admin/settings")


class SettingsUpdate(BaseModel):
    version: int
    values: dict


async def _state(values: dict, version: int) -> dict:
    return {
        "fields": settings.describe(),
        "values": values,
        "defaults": settings.defaults(),
        "version": version,
        "instances": await settings.instances(values),
    }


@router.get("", dependencies=[Depends(admin)])
async def get_settings():
    values, version = await settings.load()
    return await _state(values, version)


@router.put("")
async def put_settings(body: SettingsUpdate, principal: Principal = Depends(admin)):
    before, _ = await settings.load()
    try:
        values, version = await settings.save(body.values, body.version, principal.id)
    except settings.Invalid as e:
        raise HTTPException(400, str(e)) from None
    except settings.Conflict:
        raise HTTPException(409, "Someone else changed the settings. Reload to see their changes.") from None
    changed = [k for k in values if values[k] != before[k]]
    instruments.auth_events.add(1, {"action": "admin_settings", "outcome": "ok"})
    return {
        **await _state(values, version),
        "changed": {
            "next_recording": [settings.FIELDS[k].label for k in changed if settings.FIELDS[k].apply == "next_recording"],
            "restart": [settings.FIELDS[k].label for k in changed if settings.FIELDS[k].apply == "restart"],
        },
    }
