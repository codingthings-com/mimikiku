"""
Streaming ASR (Nemotron, cache-aware) and streaming diarization (Sortformer).

Both models natively stream in 80 ms encoder frames, which is what makes
word-level speaker attribution possible:

* ``NemotronASR`` keeps a ``StreamingASRSession`` per connection that carries
  the encoder caches and RNNT decoder state across chunks, so there are no
  context resets or cut words at chunk boundaries. Each emitted token has a
  frame index; tokens are grouped into words and committed as utterances at
  sentence ends / pauses.
* ``SubprocessDiarizer`` streams audio to the Sortformer worker, which returns
  per-frame speaker probabilities into a per-connection ``DiarizationTrack``.
* On commit, each word gets the speaker that dominates its frames and the
  utterance is split at speaker changes.
"""

import asyncio
import gc
import logging
import multiprocessing as mp
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import torch
import nemo.collections.asr as nemo_asr
from huggingface_hub import hf_hub_download

from model_management.base_model import BaseModel
from model_management.data_models import MemoryUsage
from model_management.exceptions import ModelLoadError
import log_privacy
from streaming_audio import StreamingFeatureExtractor, build_streaming_preprocessor

# Set multiprocessing start method to 'spawn' for CUDA compatibility
try:
    mp.set_start_method('spawn', force=True)
except RuntimeError:
    pass  # Already set

log = logging.getLogger(__name__)

# Encoder / diarizer output frame duration (10 ms features x 8 subsampling).
FRAME_SECONDS = 0.08
SENTENCE_END = (".", "?", "!")

# Serializes GPU work between connections sharing the ASR model.
_gpu_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Diarization track + word/speaker alignment
# ---------------------------------------------------------------------------


class DiarizationTrack:
    """Per-connection speaker activity probabilities, one row per 80 ms frame.

    Written by the diarizer's result-reader thread, read by the ASR session.
    """

    def __init__(self, n_spk: int = 4):
        self.n_spk = n_spk
        self._probs = np.zeros((256, n_spk), dtype=np.float32)
        self._len = 0
        self._lock = threading.Lock()

    @property
    def frames_available(self) -> int:
        return self._len

    def extend(self, start_frame: int, preds: np.ndarray) -> None:
        with self._lock:
            end = start_frame + len(preds)
            if end > len(self._probs):
                grown = np.zeros((max(end, 2 * len(self._probs)), self.n_spk), dtype=np.float32)
                grown[: self._len] = self._probs[: self._len]
                self._probs = grown
            self._probs[start_frame:end] = preds
            self._len = max(self._len, end)

    def window(self, start: int, end: int) -> np.ndarray:
        """Probabilities for frames [start, end), clipped to what is available."""
        with self._lock:
            start = max(0, min(start, self._len))
            end = max(start, min(end, self._len))
            return self._probs[start:end].copy()


@dataclass
class Word:
    token_ids: List[int]
    start_frame: int
    end_frame: int  # frame of the word's last token
    text: str = ""


def assign_speakers(
    words: List[Word],
    track: Optional[DiarizationTrack],
    fallback: int = -1,
    min_activity: float = 0.3,
    min_run_frames: int = 8,
) -> List[int]:
    """Pick a speaker per word from the diarization frames it spans.

    Words whose frames show no clear speaker inherit from their neighbours.
    Short runs (< ``min_run_frames``) sandwiched between the same speaker are
    treated as diarization flicker and absorbed. ``fallback`` is the speaker
    of the previously committed word and acts as the left neighbour.
    """
    if not words:
        return []
    labels: List[Optional[int]] = []
    for w in words:
        span = track.window(w.start_frame - 1, w.end_frame + 2) if track else None
        if span is None or len(span) == 0:
            labels.append(None)
            continue
        activity = span.mean(axis=0)
        best = int(activity.argmax())
        labels.append(best if activity[best] >= min_activity else None)

    # Fill gaps: forward from the previous known label, then backward.
    last = None
    for i, lab in enumerate(labels):
        if lab is None:
            labels[i] = last
        else:
            last = lab
    nxt = None
    for i in range(len(labels) - 1, -1, -1):
        if labels[i] is None:
            labels[i] = nxt
        else:
            nxt = labels[i]
    speakers = [fallback if lab is None else lab for lab in labels]

    # Absorb short flickers between two runs of the same speaker.
    runs = [[0, 0, fallback]]  # [start_idx, end_idx_exclusive, speaker]
    for i, spk in enumerate(speakers):
        if runs[-1][2] == spk:
            runs[-1][1] = i + 1
        else:
            runs.append([i, i + 1, spk])
    for r in range(1, len(runs) - 1):
        s, e, _ = runs[r]
        duration = words[e - 1].end_frame - words[s].start_frame + 1
        if runs[r - 1][2] == runs[r + 1][2] and duration < min_run_frames:
            runs[r][2] = runs[r - 1][2]
            for i in range(s, e):
                speakers[i] = runs[r - 1][2]
    return speakers


# ---------------------------------------------------------------------------
# Streaming ASR
# ---------------------------------------------------------------------------


@dataclass
class EndpointConfig:
    """When accumulated words are committed as a final utterance, and how their
    speakers are picked (80 ms frames). Admins tune most of these per recording
    (service_settings.py); a session keeps the copy it started with."""

    # Token-free frames that end an utterance. RNNT emits tokens in bursts, so
    # short gaps are common mid-sentence; sentence punctuation is the main cue.
    pause_frames: int = 25
    max_utterance_frames: int = 250  # force a commit on run-on speech (~20 s)
    diar_wait_frames: int = 40  # max wait for diarization to catch up (~3.2 s)
    min_activity: float = 0.3  # see assign_speakers
    min_run_frames: int = 8


@dataclass
class StreamingASRSession:
    """Cache-aware streaming state for one connection."""

    model: Any
    preprocessor: Any
    device: torch.device
    endpoint: EndpointConfig
    features: StreamingFeatureExtractor = field(init=False)

    def __post_init__(self):
        cfg = self.model.encoder.streaming_cfg
        self.features = StreamingFeatureExtractor(self.preprocessor, self.device)
        self.first_chunk = cfg.chunk_size[0]
        self.chunk = cfg.chunk_size[1]
        self.pre_cache = cfg.pre_encode_cache_size[1]
        self.drop_extra = cfg.drop_extra_pre_encoded
        self.out_frames_per_step = cfg.valid_out_len
        cache = self.model.encoder.get_initial_cache_state(batch_size=1, device=self.device)
        self.cache_last_channel, self.cache_last_time, self.cache_last_channel_len = cache
        self.hyp = None
        self.pending: Optional[torch.Tensor] = None  # unconsumed feature frames
        self.step = 0
        self.encoded_frames = 0  # encoder frames produced so far
        self.words: List[Word] = []  # decoded but not yet committed
        self.commit_upto = 0  # words[:commit_upto] are decided, awaiting diarization
        self.last_speaker = -1
        self.inbox: List[np.ndarray] = []
        self.lock = asyncio.Lock()

    # -- audio / model --------------------------------------------------

    def run_pending(self, final: bool = False) -> None:
        """Featurize queued audio and run every complete streaming step (blocking)."""
        audio = np.concatenate(self.inbox) if self.inbox else np.zeros(0, np.float32)
        self.inbox.clear()
        feats = self.features.push(audio)
        if final:
            tail = self.features.flush()
            feats = tail if feats is None else (feats if tail is None else torch.cat([feats, tail], 2))
        if feats is not None:
            self.pending = feats if self.pending is None else torch.cat([self.pending, feats], dim=2)

        while self.pending is not None:
            first = self.step == 0
            carried = 0 if first else self.pre_cache  # frames re-fed as pre-encode cache
            need = carried + (self.first_chunk if first else self.chunk)
            available = self.pending.shape[2]
            partial = available < need
            if partial:
                if not final or available <= carried:
                    break
                need = available
            self._step(
                self.pending[:, :, :need],
                drop_extra=carried and self.drop_extra,
                last=partial,
            )
            self.pending = self.pending[:, :, need - self.pre_cache :]

    @torch.inference_mode()
    def _step(self, chunk: torch.Tensor, drop_extra: int, last: bool) -> None:
        (
            _,
            _,
            self.cache_last_channel,
            self.cache_last_time,
            self.cache_last_channel_len,
            hyps,
        ) = self.model.conformer_stream_step(
            processed_signal=chunk,
            processed_signal_length=torch.tensor([chunk.shape[2]], device=self.device),
            cache_last_channel=self.cache_last_channel,
            cache_last_time=self.cache_last_time,
            cache_last_channel_len=self.cache_last_channel_len,
            keep_all_outputs=last,
            previous_hypotheses=self.hyp,
            drop_extra_pre_encoded=drop_extra,
            return_transcription=True,
        )
        hyp = hyps[0]
        ids = [int(t) for t in hyp.y_sequence]
        stamps = [int(t) for t in hyp.timestamp] if hyp.timestamp is not None else [0] * len(ids)
        # NeMo 2.6 carries the frame offset in the decoder state, so stamps are
        # absolute; older versions report them relative to this chunk.
        offset = self.encoded_frames if stamps and max(stamps) < self.encoded_frames else 0
        self._add_tokens(ids, [offset + s for s in stamps])
        self.encoded_frames += self.out_frames_per_step
        self.step += 1
        # Only the decoder state is needed to continue; drop the token history
        # so per-step cost stays constant on long sessions.
        hyp.y_sequence = hyp.y_sequence[:0]
        hyp.timestamp = hyp.timestamp[:0] if hyp.timestamp is not None else None
        hyp.alignments = None
        hyp.frame_confidence = None
        self.hyp = [hyp]

    def _add_tokens(self, ids: List[int], frames: List[int]) -> None:
        tokenizer = self.model.tokenizer
        for tok_id, frame, piece in zip(ids, frames, tokenizer.ids_to_tokens(ids)):
            if piece.startswith("▁") or not self.words or len(self.words) <= self.commit_upto:
                self.words.append(Word([tok_id], frame, frame))
            else:
                word = self.words[-1]
                word.token_ids.append(tok_id)
                word.end_frame = frame
        for word in self.words[self.commit_upto :]:
            word.text = tokenizer.ids_to_text(word.token_ids).strip()

    # -- endpointing ----------------------------------------------------

    def _decide_commits(self, final: bool) -> None:
        open_words = self.words[self.commit_upto :]
        if not open_words:
            return
        if final or self.encoded_frames - 1 - open_words[-1].end_frame >= self.endpoint.pause_frames:
            self.commit_upto = len(self.words)
            return
        # Commit through the last complete sentence. A sentence-final word is
        # complete once another word follows it.
        for i in range(len(self.words) - 2, self.commit_upto - 1, -1):
            if self.words[i].text.endswith(SENTENCE_END):
                self.commit_upto = i + 1
                return
        if open_words[-1].end_frame - open_words[0].start_frame >= self.endpoint.max_utterance_frames:
            self.commit_upto = len(self.words) - 1  # last word may still be growing

    def pop_segments(
        self, track: Optional[DiarizationTrack], final: bool = False
    ) -> List[Tuple[int, str, float, float]]:
        """Commit ready words and return them as (speaker, text, start_s, end_s)."""
        self._decide_commits(final)
        if self.commit_upto == 0:
            return []
        committed = self.words[: self.commit_upto]
        end_frame = committed[-1].end_frame
        diar_ready = (
            track is None
            or final
            or track.frames_available > end_frame + 2
            or self.encoded_frames - end_frame > self.endpoint.diar_wait_frames
        )
        if not diar_ready:
            return []
        self.words = self.words[self.commit_upto :]
        self.commit_upto = 0

        speakers = assign_speakers(
            committed,
            track,
            fallback=self.last_speaker,
            min_activity=self.endpoint.min_activity,
            min_run_frames=self.endpoint.min_run_frames,
        )
        self.last_speaker = speakers[-1]
        segments = []
        start = 0
        for i in range(1, len(committed) + 1):
            boundary = (
                i == len(committed)
                or speakers[i] != speakers[start]
                or committed[i - 1].text.endswith(SENTENCE_END)
            )
            if boundary:
                group = committed[start:i]
                ids = [t for w in group for t in w.token_ids]
                text = self.model.tokenizer.ids_to_text(ids).strip()
                if text:
                    segments.append(
                        (
                            speakers[start],
                            text,
                            round(group[0].start_frame * FRAME_SECONDS, 2),
                            round((group[-1].end_frame + 1) * FRAME_SECONDS, 2),
                        )
                    )
                start = i
        return segments


class NemotronASR(BaseModel):
    """NVIDIA Nemotron cache-aware streaming ASR with per-connection sessions."""

    def __init__(self, model_id: str, config: Any):
        super().__init__(model_id, config)
        self._model = None
        self._preprocessor = None
        self._is_loaded = False
        self._loading_lock = asyncio.Lock()
        self._model_memory_mb = getattr(config, "max_memory_mb", 4096) or 4096
        self._sessions: Dict[str, StreamingASRSession] = {}
        self._att_context_size = list(getattr(config, "att_context_size", [70, 13]))
        self._revision = getattr(config, "revision", None)
        self._endpoint = getattr(config, "endpoint", None) or EndpointConfig()

    async def load(self) -> None:
        if self._is_loaded:
            return

        async with self._loading_lock:
            if self._is_loaded:
                return

            try:
                start_time = time.time()
                log.info(
                    f"Loading Nemotron ASR model {self.config.model_name}@{self._revision} "
                    f"on {self.config.device} "
                    f"(att_context_size={self._att_context_size})..."
                )

                # Download the pinned commit ourselves: from_pretrained takes no
                # revision and keeps serving a stale cached .nemo.
                path = hf_hub_download(
                    repo_id=self.config.model_name,
                    filename=self.config.model_name.split("/")[-1] + ".nemo",
                    revision=self._revision,
                )
                async with _gpu_lock:
                    model = nemo_asr.models.ASRModel.restore_from(
                        path, map_location=torch.device(self.config.device)
                    )
                    model.eval()
                    model.freeze()
                    model.encoder.set_default_att_context_size(self._att_context_size)
                    model.encoder.setup_streaming_params()
                    self._preprocessor = build_streaming_preprocessor(model)
                    self._model = model

                load_time_ms = int((time.time() - start_time) * 1000)
                self.set_load_time_ms(load_time_ms)
                self._is_loaded = True
                log.info(
                    f"Nemotron ASR loaded in {load_time_ms}ms "
                    f"(chunk={self._model.encoder.streaming_cfg.chunk_size[1] * 10}ms)"
                )

            except Exception as e:
                log.error(f"Error loading Nemotron ASR: {e}")
                self._model = None
                self._is_loaded = False
                raise ModelLoadError(f"Failed to load Nemotron ASR: {e}")

    async def unload(self) -> None:
        if not self._is_loaded:
            return
        async with self._loading_lock:
            log.info("Unloading Nemotron ASR model...")
            self._sessions.clear()
            self._preprocessor = None
            if self._model is not None:
                self._model.cpu()
                del self._model
                self._model = None
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.synchronize()
                log.info("Cleared CUDA cache")
            self._is_loaded = False

    def is_loaded(self) -> bool:
        return self._is_loaded and self._model is not None

    def get_memory_usage(self) -> MemoryUsage:
        return MemoryUsage(
            total_mb=16384, used_mb=self._model_memory_mb, available_mb=8000
        )

    def open_session(self, connection_id: str, endpoint: Optional[EndpointConfig] = None) -> None:
        """Start a connection's session with its own endpoint settings (default: the model's)."""
        self._sessions[connection_id] = StreamingASRSession(
            model=self._model,
            preprocessor=self._preprocessor,
            device=torch.device(self.config.device),
            endpoint=endpoint or self._endpoint,
        )

    def _session(self, connection_id: str) -> StreamingASRSession:
        if connection_id not in self._sessions:
            self.open_session(connection_id)
        return self._sessions[connection_id]

    def ingest(self, audio: np.ndarray, connection_id: str) -> None:
        """Queue audio for a connection. Synchronous so arrival order is preserved."""
        self._session(connection_id).inbox.append(audio.astype(np.float32, copy=False))

    async def process(
        self,
        connection_id: str,
        track: Optional[DiarizationTrack] = None,
        final: bool = False,
    ) -> List[Tuple[int, str, float, float]]:
        """Run the model on queued audio; return newly finalized segments."""
        if not self.is_loaded():
            await self.load()
        self.update_access_time()
        session = self._session(connection_id)
        async with session.lock:
            async with _gpu_lock:
                await asyncio.to_thread(session.run_pending, final)
            segments = session.pop_segments(track, final=final)
        for speaker, text, start, end in segments:
            log.info(f"[{connection_id}] {log_privacy.segment(speaker, text, start, end)}")
        return segments

    async def __call__(self, _audio, track: Optional[DiarizationTrack], connection_id: str):
        """Pipeline entry point. Audio must already have been passed to ``ingest``."""
        segments = await self.process(connection_id, track)
        return {"language": "en", "transcriptions": segments}

    def cleanup_session(self, connection_id: str):
        self._sessions.pop(connection_id, None)


# ---------------------------------------------------------------------------
# Streaming diarization (subprocess)
# ---------------------------------------------------------------------------


class SubprocessDiarizer(BaseModel):
    """
    Diarizer that runs in a separate subprocess to avoid CUDA conflicts.
    Audio is streamed in fire-and-forget; a reader thread files the returned
    frame probabilities into per-connection ``DiarizationTrack``s.
    """

    def __init__(self, model_id: str, config: Any):
        super().__init__(model_id, config)
        self._process: Optional[mp.Process] = None
        self._audio_queue: Optional[mp.Queue] = None
        self._result_queue: Optional[mp.Queue] = None
        self._stop_event: Optional[mp.Event] = None
        self._reader: Optional[threading.Thread] = None
        self._loading_lock = asyncio.Lock()
        self._model_memory_mb = getattr(config, "max_memory_mb", 1024) or 1024
        self._latency_preset = getattr(config, "diar_latency_preset", "low")
        self._revision = getattr(config, "revision", None)
        self._tracks: Dict[str, DiarizationTrack] = {}

    async def load(self) -> None:
        if self._is_loaded:
            return

        async with self._loading_lock:
            if self._is_loaded:
                return

            try:
                from diarize_worker import diarize_worker

                start_time = time.time()
                log.info(
                    f"Starting diarization subprocess for {self.config.model_name}@{self._revision}..."
                )

                self._audio_queue = mp.Queue()
                self._result_queue = mp.Queue()
                self._stop_event = mp.Event()

                # HF_TOKEN is set globally in the environment by config.py
                # and inherited by the subprocess automatically.
                self._process = mp.Process(
                    target=diarize_worker,
                    args=(
                        self._audio_queue,
                        self._result_queue,
                        self.config.model_name,
                        self._stop_event,
                        self._latency_preset,
                    ),
                    kwargs={"revision": self._revision},
                    daemon=True,
                )
                self._process.start()

                try:
                    msg = await asyncio.to_thread(self._result_queue.get, True, 120.0)
                except queue.Empty:
                    self._process.terminate()
                    raise ModelLoadError("Timeout waiting for diarization subprocess")
                if msg[0] == "__error__":
                    raise ModelLoadError(f"Subprocess failed to load model: {msg[1]}")
                log.info("Diarization subprocess ready")

                self._reader = threading.Thread(
                    target=self._read_results,
                    args=(self._result_queue, self._stop_event),
                    name="diarization-results",
                    daemon=True,
                )
                self._reader.start()

                load_time_ms = int((time.time() - start_time) * 1000)
                self.set_load_time_ms(load_time_ms)
                self._is_loaded = True
                log.info(
                    f"SubprocessDiarizer loaded in {load_time_ms}ms (PID: {self._process.pid})"
                )

            except Exception as e:
                log.error(f"Error starting diarization subprocess: {e}")
                self._is_loaded = False
                raise ModelLoadError(f"Failed to start diarization subprocess: {e}")

    def _read_results(self, result_queue: mp.Queue, stop_event) -> None:
        while not stop_event.is_set():
            try:
                connection_id, start_frame, preds = result_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            except (EOFError, OSError, ValueError):
                break
            track = self._tracks.get(connection_id)
            if track is not None:
                track.extend(start_frame, preds)

    async def unload(self) -> None:
        if not self._is_loaded:
            return

        async with self._loading_lock:
            try:
                if self._stop_event:
                    self._stop_event.set()
                if self._audio_queue:
                    self._audio_queue.put(None)  # Shutdown signal
                if self._process and self._process.is_alive():
                    self._process.join(timeout=5.0)
                    if self._process.is_alive():
                        self._process.terminate()
                        log.warning("Had to terminate diarization subprocess")
                if self._reader:
                    self._reader.join(timeout=2.0)
            except Exception as e:
                log.error(f"Error stopping diarization subprocess: {e}")
            finally:
                self._process = None
                self._audio_queue = None
                self._result_queue = None
                self._stop_event = None
                self._reader = None
                self._is_loaded = False
                self._tracks.clear()
                gc.collect()

    def is_loaded(self) -> bool:
        return (
            self._is_loaded and self._process is not None and self._process.is_alive()
        )

    def get_memory_usage(self) -> MemoryUsage:
        return MemoryUsage(
            total_mb=16384, used_mb=self._model_memory_mb, available_mb=8000
        )

    def submit(self, audio: np.ndarray, connection_id: str) -> Optional[DiarizationTrack]:
        """Stream audio to the worker; return the connection's track (non-blocking)."""
        if not self.is_loaded():
            log.warning("Diarization subprocess not running; skipping diarization")
            return None
        self.update_access_time()
        track = self._tracks.get(connection_id)
        if track is None:
            track = self._tracks[connection_id] = DiarizationTrack()
        self._audio_queue.put((connection_id, audio.astype(np.float32, copy=False).tobytes()))
        return track

    def cleanup_session(self, connection_id: str) -> None:
        """Send cleanup signal to subprocess for this connection."""
        self._tracks.pop(connection_id, None)
        if self._audio_queue and self._is_loaded:
            try:
                self._audio_queue.put((connection_id, None))
                log.info(f"Sent cleanup signal for session {connection_id}")
            except Exception:
                pass
