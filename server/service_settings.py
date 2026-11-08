"""Service settings that admins can change: transcription and diarization tuning.

Stored as one Mongo document (`settings`, _id "service") shared by every server
instance. Each field says when a change takes effect:

- `next_recording`: read when a /listen connection starts (endpointing and
  speaker smoothing), so new recordings pick it up right away.
- `restart`: baked into the loaded models (ASR chunk size, diarizer latency,
  model idle TTL). Applied when the server starts; until then the instance
  reports the change as pending, and the admin page says to restart it.

Reloading models inside a running server is possible for some of these but
risky (sessions hold the models, CUDA memory), so we don't.

Each instance records itself in `instances` (heartbeat, the restart-class values
it booted with, active recordings) so the admin page can show which servers
still need a restart. Kept free of torch/NeMo imports.
"""

import asyncio
import logging
import os
import socket
import uuid
from dataclasses import dataclass
from datetime import timedelta

from accounts import db
from accounts.security import now

log = logging.getLogger(__name__)

DOC_ID = "service"
HEARTBEAT_S = 30
INSTANCE_TTL_S = 120  # an instance that stops heartbeating drops off the list

FRAME_S = 0.08  # ASR and diarizer frame


@dataclass(frozen=True)
class Field:
    label: str
    help: str
    default: object
    apply: str  # next_recording | restart
    group: str
    min: float | None = None
    max: float | None = None
    step: float | None = None
    unit: str = ""
    choices: tuple = ()  # ((value, label), ...)


FIELDS: dict[str, Field] = {
    "asr_chunk": Field(
        "Transcription chunk",
        "How much audio the speech model reads per step. Larger is more accurate; smaller shows words sooner "
        "but costs more GPU per recording.",
        "1.12",
        "restart",
        "Models",
        choices=(("1.12", "1.12 s — most accurate"), ("0.56", "0.56 s — balanced"), ("0.16", "0.16 s — fastest")),
    ),
    "diar_preset": Field(
        "Speaker detection latency",
        "How quickly speaker labels follow the audio. Faster is a little less stable.",
        "low",
        "restart",
        "Models",
        choices=(("low", "Standard — about 1 s"), ("ultra_low", "Fast — about 0.3 s")),
    ),
    "asr_idle_ttl_min": Field(
        "Unload idle speech model after",
        "Frees GPU memory when nobody has recorded for this long; the next recording then waits for the model "
        "to load again.",
        5,
        "restart",
        "Models",
        min=1,
        max=120,
        step=1,
        unit="min",
    ),
    "pause_s": Field(
        "End a line after a pause of",
        "Silence that ends the current line. Lines also end at sentence ends. Capped at 3 s because the web "
        "app sends 3.5 s of silence when you stop, to finish the last line.",
        2.0,
        "next_recording",
        "Lines",
        min=0.5,
        max=3.0,
        step=0.1,
        unit="s",
    ),
    "max_line_s": Field(
        "Break run-on lines after",
        "Forces a line break in long speech without sentence ends or pauses.",
        20.0,
        "next_recording",
        "Lines",
        min=5,
        max=60,
        step=1,
        unit="s",
    ),
    "min_switch_s": Field(
        "Ignore speaker switches shorter than",
        "Brief flickers to another speaker in the middle of someone's speech are treated as noise.",
        0.64,
        "next_recording",
        "Speakers",
        min=0,
        max=2,
        step=0.08,
        unit="s",
    ),
    "min_activity": Field(
        "Speaker confidence",
        "How sure the diarizer must be before a word is credited to a speaker; less certain words take their "
        "neighbours' speaker. Higher means fewer but more confident speaker changes.",
        0.3,
        "next_recording",
        "Speakers",
        min=0.1,
        max=0.7,
        step=0.05,
    ),
}

RESTART_FIELDS = tuple(k for k, f in FIELDS.items() if f.apply == "restart")


class Invalid(ValueError):
    pass


def defaults() -> dict:
    return {k: f.default for k, f in FIELDS.items()}


def validate(values: dict) -> dict:
    """A complete, checked settings dict from (possibly partial) input."""
    out = defaults()
    for key, value in values.items():
        field = FIELDS.get(key)
        if field is None:
            raise Invalid(f"Unknown setting: {key}")
        if field.choices:
            if value not in [c[0] for c in field.choices]:
                raise Invalid(f"{field.label}: choose one of {', '.join(c[0] for c in field.choices)}")
        else:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise Invalid(f"{field.label}: must be a number")
            if not field.min <= value <= field.max:
                raise Invalid(f"{field.label}: must be between {field.min:g} and {field.max:g}")
            value = int(value) if isinstance(field.default, int) else float(value)
        out[key] = value
    return out


def _clean(stored: dict) -> dict:
    """Stored values with anything invalid (e.g. from an older version) replaced by defaults."""
    out = defaults()
    for key, value in (stored or {}).items():
        try:
            out.update({key: validate({key: value})[key]})
        except Invalid:
            log.warning(f"Ignoring invalid stored setting {key}={value!r}")
    return out


async def load() -> tuple[dict, int]:
    """(values, version). Version 0 means never saved: all defaults."""
    doc = await db.get().settings.find_one({"_id": DOC_ID})
    if not doc:
        return defaults(), 0
    return _clean(doc.get("values")), doc.get("version", 0)


class Conflict(Exception):
    pass


async def save(values: dict, expected_version: int, user_id: str) -> tuple[dict, int]:
    """Store a full settings dict if nobody saved since `expected_version`."""
    values = validate(values)
    coll = db.get().settings
    if expected_version == 0:
        doc = {"_id": DOC_ID, "values": values, "version": 1, "updatedAt": now(), "updatedBy": user_id}
        try:
            await coll.insert_one(doc)
        except Exception:
            raise Conflict() from None
        return values, 1
    result = await coll.update_one(
        {"_id": DOC_ID, "version": expected_version},
        {"$set": {"values": values, "updatedAt": now(), "updatedBy": user_id}, "$inc": {"version": 1}},
    )
    if result.matched_count != 1:
        raise Conflict()
    return values, expected_version + 1


# ---------------------------------------------------------------------------
# What a recording uses (next_recording fields), in model units
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RecordingSettings:
    pause_frames: int
    max_utterance_frames: int
    min_run_frames: int
    min_activity: float


def recording_settings(values: dict) -> RecordingSettings:
    return RecordingSettings(
        pause_frames=max(1, round(values["pause_s"] / FRAME_S)),
        max_utterance_frames=max(1, round(values["max_line_s"] / FRAME_S)),
        min_run_frames=round(values["min_switch_s"] / FRAME_S),
        min_activity=float(values["min_activity"]),
    )


# ---------------------------------------------------------------------------
# Instances: who is running with which restart-class values
# ---------------------------------------------------------------------------


class Instance:
    """This server process, as listed on the admin page."""

    def __init__(self):
        self.id = uuid.uuid4().hex
        self.name = os.environ.get("MIMIKIKU_INSTANCE") or socket.gethostname()
        self.booted: dict = {}
        self.version = 0
        self._task: asyncio.Task | None = None

    async def register(self, values: dict, version: int, active_recordings) -> None:
        """Record the restart-class values this process applied, then heartbeat."""
        self.booted = {k: values[k] for k in RESTART_FIELDS}
        self.version = version
        await db.get().instances.create_index("heartbeatAt", expireAfterSeconds=INSTANCE_TTL_S)
        await self._beat(active_recordings(), startup=True)
        self._task = asyncio.create_task(self._loop(active_recordings))

    async def _beat(self, active: int, startup: bool = False) -> None:
        update = {"name": self.name, "heartbeatAt": now(), "activeRecordings": active}
        if startup:
            update |= {"startedAt": now(), "booted": self.booted, "bootedVersion": self.version, "pid": os.getpid()}
        await db.get().instances.update_one({"_id": self.id}, {"$set": update}, upsert=True)

    async def _loop(self, active_recordings) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_S)
            try:
                await self._beat(active_recordings())
            except Exception as e:
                log.warning(f"Instance heartbeat failed: {e}")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        try:
            await db.get().instances.delete_one({"_id": self.id})
        except Exception:
            pass


async def instances(values: dict) -> list[dict]:
    """Live instances and which saved restart-class changes each is still missing."""
    cutoff = now() - timedelta(seconds=INSTANCE_TTL_S)
    docs = await db.get().instances.find({"heartbeatAt": {"$gt": cutoff}}).sort("startedAt", 1).to_list(None)
    out = []
    for d in docs:
        booted = d.get("booted", {})
        pending = [FIELDS[k].label for k in RESTART_FIELDS if booted.get(k, FIELDS[k].default) != values[k]]
        out.append(
            {
                "name": d.get("name"),
                "startedAt": d.get("startedAt"),
                "heartbeatAt": d.get("heartbeatAt"),
                "activeRecordings": d.get("activeRecordings", 0),
                "pendingRestart": pending,
            }
        )
    return out


def describe() -> list[dict]:
    """Field metadata for the admin form."""
    return [
        {
            "key": k,
            "label": f.label,
            "help": f.help,
            "default": f.default,
            "apply": f.apply,
            "group": f.group,
            "min": f.min,
            "max": f.max,
            "step": f.step,
            "unit": f.unit,
            "choices": [{"value": v, "label": l} for v, l in f.choices],
        }
        for k, f in FIELDS.items()
    ]
