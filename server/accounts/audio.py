"""Optional recording audio, kept for later corrections. Off unless STORE_AUDIO=1.

Audio is the stream the pipeline actually transcribed (everything after the
socket reports ready), so segment times line up with it. It's stored in GridFS
as raw 16-bit mono PCM and wrapped in a WAV header on download. About 115MB per
hour, so it is opt-in.
"""

import logging
import os
import struct

import numpy as np
from gridfs import AsyncGridFSBucket

from telemetry import instruments

from . import db

log = logging.getLogger(__name__)

BUCKET = "audio"


def enabled() -> bool:
    return os.environ.get("STORE_AUDIO", "").lower() in ("1", "true", "yes")


def bucket():
    return AsyncGridFSBucket(db.get(), bucket_name=BUCKET)


def to_pcm16(samples: np.ndarray) -> bytes:
    clipped = np.clip(np.asarray(samples, dtype=np.float32).reshape(-1), -1.0, 1.0)
    return (clipped * 32767.0).astype("<i2").tobytes()


def wav_header(data_bytes: int, sample_rate: int) -> bytes:
    """44-byte header for 16-bit mono PCM."""
    return (
        b"RIFF"
        + struct.pack("<I", 36 + data_bytes)
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16)
        + b"data"
        + struct.pack("<I", data_bytes)
    )


class AudioUpload:
    """Streams one recording's audio into GridFS as it arrives."""

    def __init__(self, user_id: str, recording_id: str, sample_rate: int):
        self.sample_rate = sample_rate
        self._stream = bucket().open_upload_stream(
            f"{recording_id}.pcm",
            metadata={"userId": user_id, "recording": recording_id, "sampleRate": sample_rate},
        )
        self._failed = False

    @property
    def file_id(self):
        return self._stream._id

    async def write(self, samples: np.ndarray) -> None:
        if self._failed:
            return
        try:
            await self._stream.write(to_pcm16(samples))
        except Exception as e:
            instruments.store_errors.add(1, {"op": "audio"})
            log.error(f"Audio write failed, dropping this recording's audio: {e}")
            self._failed = True

    async def finish(self, keep: bool) -> bool:
        """Close the file, or discard it when `keep` is false or writing failed."""
        try:
            if keep and not self._failed:
                await self._stream.close()
                return True
            await self._stream.abort()
        except Exception as e:
            log.error(f"Audio finish failed: {e}")
        return False


async def delete_files(file_ids) -> None:
    for file_id in file_ids:
        try:
            await bucket().delete(file_id)
        except Exception as e:
            log.warning(f"Could not delete audio {file_id}: {e}")


async def delete_for_user(user_id: str) -> None:
    files = await bucket().find({"metadata.userId": user_id}).to_list(None)
    await delete_files([f._id for f in files])


async def stream_wav(file_id):
    """(length, async iterator of bytes) for a stored recording, as WAV."""
    out = await bucket().open_download_stream(file_id)
    rate = (out.metadata or {}).get("sampleRate", 16000)

    async def chunks():
        yield wav_header(out.length, rate)
        while chunk := await out.readchunk():
            yield chunk

    return 44 + out.length, chunks()
