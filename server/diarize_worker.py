"""
Diarization Worker Subprocess

Runs Sortformer diarization model in a separate process to avoid CUDA conflicts
with the ASR model. Uses multiprocessing queues for IPC.

Protocol:
    audio_queue  <- (connection_id, float32 audio bytes)   stream audio
                    (connection_id, None)                  drop session state
                    None                                   shut down
    result_queue -> ("__ready__", None) / ("__error__", msg) once at startup
                    (connection_id, start_frame, preds)     per streaming step,
                    where preds is a float32 [n_frames, n_spk] array of
                    speaker activity probabilities (one frame = 80 ms).

Usage:
    This module is spawned as a subprocess by SubprocessDiarizer.
    Do not import this directly in the main process.
"""

import os
import logging
import multiprocessing as mp
from typing import Dict, List, Optional, Tuple

import numpy as np
import torch

log = logging.getLogger(__name__)

# Streaming configurations from the diar_streaming_sortformer_4spk-v2(.1) model
# card (all use spkcache_len=188, chunk_left_context=1). Values are in 80 ms
# diarization frames; latency = (chunk_len + chunk_right_context) * 80 ms.
LATENCY_PRESETS: Dict[str, Dict[str, int]] = {
    # 0.32 s latency
    "ultra_low": dict(chunk_len=3, chunk_right_context=1, fifo_len=188, spkcache_update_period=144),
    # 1.04 s latency - matches Nemotron's 1.12 s streaming chunk
    "low": dict(chunk_len=6, chunk_right_context=7, fifo_len=188, spkcache_update_period=144),
    # 10 s latency
    "high": dict(chunk_len=124, chunk_right_context=1, fifo_len=124, spkcache_update_period=124),
}


def configure_streaming(model, preset: str) -> None:
    """Apply a latency preset to a SortformerEncLabelModel."""
    if preset not in LATENCY_PRESETS:
        raise ValueError(f"Unknown diarization latency preset '{preset}'")
    modules = model.sortformer_modules
    modules.spkcache_len = 188
    modules.chunk_left_context = 1
    for key, value in LATENCY_PRESETS[preset].items():
        setattr(modules, key, value)
    modules._check_streaming_parameters()


class _Session:
    def __init__(self, model, preprocessor, device):
        from streaming_audio import StreamingFeatureExtractor

        self.features = StreamingFeatureExtractor(preprocessor, device)
        self.state = model.sortformer_modules.init_streaming_state(batch_size=1, device=device)
        self.buffer: Optional[torch.Tensor] = None  # [1, n_feats, T] feature frames
        self.buffer_start = 0  # absolute feature frame index of buffer[..., 0]
        self.chunk_start = 0  # absolute feature frame where the next chunk begins
        self.frames_out = 0  # diarization frames emitted so far


class SortformerStreamer:
    """Frame-level streaming diarization for many concurrent sessions."""

    def __init__(self, model, device, preset: str = "low"):
        from streaming_audio import build_streaming_preprocessor

        self.model = model
        self.device = device
        configure_streaming(model, preset)
        self.preprocessor = build_streaming_preprocessor(model)
        modules = model.sortformer_modules
        self.n_spk = modules.n_spk
        sub = modules.subsampling_factor
        self.chunk_feats = modules.chunk_len * sub
        self.left_feats = modules.chunk_left_context * sub
        self.right_feats = modules.chunk_right_context * sub
        self.sessions: Dict[str, _Session] = {}

    def drop(self, connection_id: str) -> bool:
        return self.sessions.pop(connection_id, None) is not None

    def process(self, connection_id: str, audio: np.ndarray) -> List[Tuple[int, np.ndarray]]:
        """Feed audio; return [(start_frame, preds[n, n_spk])] for completed chunks."""
        session = self._session(connection_id)
        self._append(session, session.features.push(audio))
        return self._run(session, final=False)

    def flush(self, connection_id: str) -> List[Tuple[int, np.ndarray]]:
        """Process whatever audio is left at the end of a stream."""
        session = self._session(connection_id)
        self._append(session, session.features.flush())
        return self._run(session, final=True)

    def _session(self, connection_id: str) -> _Session:
        if connection_id not in self.sessions:
            log.info(f"Initializing state for {connection_id} on {self.device}")
            self.sessions[connection_id] = _Session(self.model, self.preprocessor, self.device)
        return self.sessions[connection_id]

    @staticmethod
    def _append(session: _Session, feats: Optional[torch.Tensor]) -> None:
        if feats is None or feats.shape[2] == 0:
            return
        if session.buffer is None:
            session.buffer = feats
        else:
            session.buffer = torch.cat([session.buffer, feats], dim=2)

    @torch.no_grad()
    def _run(self, session: _Session, final: bool) -> List[Tuple[int, np.ndarray]]:
        results = []
        if session.buffer is None:
            return results
        while True:
            buffer_end = session.buffer_start + session.buffer.shape[2]
            stt = session.chunk_start
            if stt >= buffer_end:
                break
            end = stt + self.chunk_feats
            if end + self.right_feats > buffer_end:
                if not final:
                    break  # wait for the full right context
                end = min(end, buffer_end)
            left = min(self.left_feats, stt)
            right = min(self.right_feats, buffer_end - end)
            lo = stt - left - session.buffer_start
            chunk = session.buffer[:, :, lo : lo + left + (end - stt) + right]
            length = torch.tensor([chunk.shape[2]], device=self.device)
            empty = torch.zeros((1, 0, self.n_spk), device=self.device)
            session.state, chunk_preds = self.model.forward_streaming_step(
                processed_signal=chunk.transpose(1, 2),
                processed_signal_length=length,
                streaming_state=session.state,
                total_preds=empty,
                left_offset=left,
                right_offset=right,
            )
            preds = chunk_preds[0].float().cpu().numpy()
            results.append((session.frames_out, preds))
            session.frames_out += preds.shape[0]
            session.chunk_start = end

            # Keep only the left context needed by the next chunk.
            keep_from = max(0, session.chunk_start - self.left_feats)
            drop = keep_from - session.buffer_start
            if drop > 0:
                session.buffer = session.buffer[:, :, drop:]
                session.buffer_start = keep_from
        return results


def diarize_worker(
    audio_queue: mp.Queue,
    result_queue: mp.Queue,
    model_name: str = "nvidia/diar_streaming_sortformer_4spk-v2.1",
    stop_event: Optional[mp.Event] = None,
    latency_preset: str = "low",
    *,
    revision: Optional[str] = None,
    hf_token: Optional[str] = None,
):
    """
    Worker function that runs in subprocess.

    Args:
        audio_queue: Queue to receive (connection_id, audio bytes) tuples
        result_queue: Queue to send (connection_id, start_frame, preds) results
        model_name: NeMo model name to load
        stop_event: Event to signal shutdown
        latency_preset: Key into LATENCY_PRESETS
        revision: Hub commit to load (keyword-only); None = main
        hf_token: Hugging Face token for model downloads (keyword-only)
    """
    # Configure logging for subprocess
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [DIARIZER] %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    log = logging.getLogger(__name__)

    log.info(f"Diarization worker starting (PID: {os.getpid()})")
    log.info(
        f"CUDA_VISIBLE_DEVICES={os.environ.get('CUDA_VISIBLE_DEVICES', 'not set')}"
    )
    log.info(f"torch.cuda.is_available()={torch.cuda.is_available()}")

    # Determine device
    device_str = "cuda" if torch.cuda.is_available() else "cpu"
    device = torch.device(device_str)
    log.info(f"Using device: {device}")

    # Load model
    try:
        from huggingface_hub import hf_hub_download
        from nemo.collections.asr.models import SortformerEncLabelModel

        log.info(f"Loading Sortformer model: {model_name}@{revision} on {device}")

        # huggingface_hub reads HF_TOKEN from the environment. The env var is
        # set by config.py at startup and inherited by this subprocess.
        # If hf_token was passed explicitly, ensure the env var is set.
        if hf_token and not os.environ.get("HF_TOKEN"):
            os.environ["HF_TOKEN"] = hf_token

        # Download the pinned commit ourselves: NeMo's from_pretrained takes no
        # revision and keeps serving a stale cached .nemo.
        path = hf_hub_download(
            repo_id=model_name,
            filename=model_name.split("/")[-1] + ".nemo",
            revision=revision,
        )
        model = SortformerEncLabelModel.restore_from(path, map_location=device)
        model.eval()
        model.to(device)
        model.streaming_mode = True
        streamer = SortformerStreamer(model, device, latency_preset)

        log.info(
            f"Sortformer model loaded successfully on {device} "
            f"(latency preset '{latency_preset}': {LATENCY_PRESETS[latency_preset]})"
        )

    except Exception as e:
        log.error(f"Failed to load diarization model: {e}")
        result_queue.put(("__error__", str(e)))
        return

    # Signal ready
    result_queue.put(("__ready__", None))

    # Main processing loop
    while True:
        try:
            # Check for stop signal
            if stop_event and stop_event.is_set():
                log.info("Stop event received, shutting down")
                break

            # Get audio from queue (blocking with timeout)
            try:
                msg = audio_queue.get(timeout=1.0)
            except Exception:
                continue

            if msg is None:
                log.info("Received shutdown signal")
                break

            connection_id, audio_data = msg

            # Handle cleanup command
            if audio_data is None:
                if streamer.drop(connection_id):
                    log.info(f"Cleaned up session state for {connection_id}")
                continue

            audio = np.frombuffer(audio_data, dtype=np.float32)
            try:
                for start_frame, preds in streamer.process(connection_id, audio):
                    result_queue.put((connection_id, start_frame, preds))
            except Exception as e:
                # Drop the broken session; the next chunk starts a fresh one.
                log.error(f"Error processing audio for {connection_id}: {e}")
                streamer.drop(connection_id)

        except Exception as e:
            log.error(f"Worker loop error: {e}")
            continue

    # Cleanup on shutdown
    log.info("Diarization worker shutting down, cleaning up GPU memory...")

    # Clear session states
    if "streamer" in locals():
        streamer.sessions.clear()
        del streamer

    # Delete model and clear GPU memory
    if 'model' in locals():
        if hasattr(model, 'cpu'):
            model.cpu()
        del model

    # Force garbage collection
    import gc
    gc.collect()

    # Clear CUDA cache
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.synchronize()
        log.info("Cleared CUDA cache in diarization worker")

    log.info("Diarization worker shutdown complete")


if __name__ == "__main__":
    # For testing standalone
    print("Diarization worker module - run via SubprocessDiarizer")
