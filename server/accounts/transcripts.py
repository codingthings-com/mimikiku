"""Per-user transcripts.

One document per transcript:

    {_id, userId, title, createdAt, updatedAt,
     recordings: [{id, startedAt, audioId?}], recordingCount,
     segments: [{id, recording, speaker, text, start, end}], segmentCount,
     names: {"<recording>:<speaker>": "Name"}}

Each /listen connection is one recording. Segment times are seconds from the
start of their recording, and speaker numbers only mean something within one
recording (the diarizer restarts per connection), hence the `names` keys.
A long meeting is a few hundred KB, far below Mongo's 16MB document limit.
"""

import asyncio
import logging
import re

from telemetry import instruments

from . import db
from .security import new_id, now

log = logging.getLogger(__name__)

MAX_TITLE = 200
MAX_NAME = 60
MAX_TEXT = 10_000
NAME_KEY = re.compile(r"^[A-Za-z0-9_-]+:-?\d+$")


def _recording(rec: dict) -> dict:
    return {"id": rec["id"], "startedAt": rec["startedAt"], "audio": bool(rec.get("audioId"))}


def public(doc: dict) -> dict:
    return {
        "id": doc["_id"],
        "title": doc.get("title", ""),
        "createdAt": doc["createdAt"],
        "updatedAt": doc["updatedAt"],
        "recordings": [_recording(r) for r in doc.get("recordings", [])],
        "segments": doc.get("segments", []),
        "names": doc.get("names", {}),
    }


def summary(doc: dict) -> dict:
    first = (doc.get("segments") or [None])[0]
    return {
        "id": doc["_id"],
        "title": doc.get("title", ""),
        "createdAt": doc["createdAt"],
        "updatedAt": doc["updatedAt"],
        "segmentCount": doc.get("segmentCount", 0),
        "recordingCount": doc.get("recordingCount", 0),
        "preview": first["text"] if first else "",
    }


def _new_doc(user_id: str, title: str = "", recordings: list | None = None) -> dict:
    at = now()
    recordings = recordings or []
    return {
        "_id": "mk_" + new_id(),
        "userId": user_id,
        "title": title,
        "createdAt": at,
        "updatedAt": at,
        "recordings": recordings,
        "recordingCount": len(recordings),
        "segments": [],
        "segmentCount": 0,
        "names": {},
    }


async def create(user_id: str, title: str = "") -> dict:
    doc = _new_doc(user_id, title)
    await db.get().transcripts.insert_one(doc)
    return doc


async def list_for(user_id: str, query: str = "", limit: int = 100, offset: int = 0) -> list[dict]:
    """Newest first. `query` matches the title or any line, case-insensitively."""
    filt: dict = {"userId": user_id}
    if query.strip():
        pattern = {"$regex": re.escape(query.strip()), "$options": "i"}
        filt["$or"] = [{"title": pattern}, {"segments.text": pattern}]
    cursor = (
        db.get()
        .transcripts.find(filt, {"segments": {"$slice": 1}, "names": 0, "recordings": 0})
        .sort("updatedAt", -1)
        .skip(offset)
        .limit(limit)
    )
    return await cursor.to_list(None)


async def get(user_id: str, transcript_id: str) -> dict | None:
    return await db.get().transcripts.find_one({"_id": transcript_id, "userId": user_id})


async def exists(user_id: str, transcript_id: str) -> bool:
    return await db.get().transcripts.count_documents({"_id": transcript_id, "userId": user_id}, limit=1) == 1


async def update(user_id: str, transcript_id: str, title: str | None = None, names: dict | None = None) -> bool:
    """Set the title and/or speaker names; an empty name removes the custom name."""
    set_: dict = {"updatedAt": now()}
    unset: dict = {}
    if title is not None:
        set_["title"] = title.strip()[:MAX_TITLE]
    for key, name in (names or {}).items():
        name = name.strip()[:MAX_NAME]
        if name:
            set_[f"names.{key}"] = name
        else:
            unset[f"names.{key}"] = ""
    change = {"$set": set_}
    if unset:
        change["$unset"] = unset
    result = await db.get().transcripts.update_one({"_id": transcript_id, "userId": user_id}, change)
    return result.matched_count == 1


async def edit_segment(user_id: str, transcript_id: str, segment_id: str, text: str) -> bool:
    result = await db.get().transcripts.update_one(
        {"_id": transcript_id, "userId": user_id, "segments.id": segment_id},
        {"$set": {"segments.$.text": text.strip()[:MAX_TEXT], "updatedAt": now()}},
    )
    return result.matched_count == 1


async def delete_segment(user_id: str, transcript_id: str, segment_id: str) -> bool:
    result = await db.get().transcripts.update_one(
        {"_id": transcript_id, "userId": user_id, "segments.id": segment_id},
        {"$pull": {"segments": {"id": segment_id}}, "$inc": {"segmentCount": -1}, "$set": {"updatedAt": now()}},
    )
    return result.matched_count == 1


async def delete(user_id: str, transcript_id: str) -> bool:
    from . import audio

    doc = await db.get().transcripts.find_one_and_delete({"_id": transcript_id, "userId": user_id})
    if not doc:
        return False
    await audio.delete_files([r["audioId"] for r in doc.get("recordings", []) if r.get("audioId")])
    return True


async def delete_for_user(user_id: str) -> None:
    await db.get().transcripts.delete_many({"userId": user_id})


async def audio_id(user_id: str, transcript_id: str, recording_id: str):
    doc = await db.get().transcripts.find_one(
        {"_id": transcript_id, "userId": user_id}, {"recordings": 1}
    )
    for rec in (doc or {}).get("recordings", []):
        if rec["id"] == recording_id:
            return rec.get("audioId")
    return None


class RecordingWriter:
    """Saves one /listen connection's segments as a recording.

    With no transcript id, a new transcript is created; otherwise the recording
    is appended to that transcript. Nothing is written until the first segments
    arrive, so a connection that never hears speech leaves nothing behind.
    Hold `lock` around open/stamp/send/append so batches stay in order.
    """

    def __init__(self, user_id: str, transcript_id: str | None = None):
        self.user_id = user_id
        self.transcript_id = transcript_id
        self.recording = {"id": new_id(6), "startedAt": now()}
        self.lock = asyncio.Lock()
        self._count = 0
        self._opened = False
        self._dead = False

    @property
    def saved(self) -> bool:
        """The recording exists in a transcript (it got segments and the transcript is still there)."""
        return self._opened and not self._dead

    async def open(self) -> dict | None:
        """On the first call, create the transcript or add this recording to it.

        Returns the `{"type": "transcript", ...}` event to send to the client
        then, or None on later calls and when the transcript is gone.
        """
        if self._opened or self._dead:
            return None
        self._opened = True
        transcripts = db.get().transcripts
        try:
            if self.transcript_id is None:
                doc = _new_doc(self.user_id, recordings=[self.recording])
                await transcripts.insert_one(doc)
                self.transcript_id = doc["_id"]
            else:
                result = await transcripts.update_one(
                    {"_id": self.transcript_id, "userId": self.user_id},
                    {"$push": {"recordings": self.recording}, "$inc": {"recordingCount": 1}, "$set": {"updatedAt": now()}},
                )
                if result.matched_count == 0:
                    log.info(f"Transcript {self.transcript_id} is gone; recording {self.recording['id']} won't be saved")
                    self._dead = True
                    return None
        except Exception as e:
            instruments.store_errors.add(1, {"op": "open"})
            log.error(f"Could not save recording {self.recording['id']}; transcription continues unsaved: {e}")
            self._dead = True
            return None
        recording = {**_recording(self.recording), "startedAt": self.recording["startedAt"].isoformat()}
        return {"type": "transcript", "id": self.transcript_id, "recording": recording}

    def stamp(self, segments: list[dict]) -> list[dict]:
        """Give each segment an id and its recording, in place."""
        rid = self.recording["id"]
        for seg in segments:
            self._count += 1
            seg["id"] = f"{rid}:{self._count}"
            seg["recording"] = rid
        return segments

    async def append(self, segments: list[dict]) -> None:
        if not self.saved or not segments:
            return
        stored = [
            {k: seg[k] for k in ("id", "recording", "speaker", "text", "start", "end")} for seg in segments
        ]
        try:
            result = await db.get().transcripts.update_one(
                {"_id": self.transcript_id, "userId": self.user_id},
                {
                    "$push": {"segments": {"$each": stored}},
                    "$inc": {"segmentCount": len(stored)},
                    "$set": {"updatedAt": now()},
                },
            )
        except Exception as e:
            instruments.store_errors.add(1, {"op": "append"})
            # Keep going: the next batch may get through. The client still has these lines.
            log.error(f"Could not save {len(stored)} segments to {self.transcript_id}: {e}")
            return
        if result.matched_count == 0:
            log.info(f"Transcript {self.transcript_id} was deleted mid-recording; no longer saving")
            self._dead = True

    async def attach_audio(self, file_id) -> bool:
        """Link a stored audio file to this recording. False if the recording wasn't saved."""
        if not self.saved:
            return False
        result = await db.get().transcripts.update_one(
            {"_id": self.transcript_id, "userId": self.user_id, "recordings.id": self.recording["id"]},
            {"$set": {"recordings.$.audioId": file_id}},
        )
        return result.matched_count == 1
