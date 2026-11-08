"""
Incremental feature extraction for streaming NeMo models.

Both Nemotron ASR and Sortformer use a log-mel preprocessor with
``normalize: NA`` (no per-utterance statistics), so features can be computed
incrementally.  Naively preprocessing each network chunk on its own zero-pads
the STFT at every chunk edge and corrupts the frames around each boundary.
``StreamingFeatureExtractor`` instead only emits frames whose analysis window
is fully covered by received audio, and keeps a little left context so the
output matches whole-signal preprocessing.

This module only depends on numpy/torch so it is safe to import from the
diarization subprocess.
"""

import copy
import math
from typing import Optional

import numpy as np
import torch
from omegaconf import OmegaConf, open_dict


def build_streaming_preprocessor(model) -> torch.nn.Module:
    """Clone a NeMo model's preprocessor with dither/padding disabled."""
    cfg = copy.deepcopy(model._cfg.preprocessor)
    OmegaConf.set_struct(cfg, False)
    with open_dict(cfg):
        cfg.dither = 0.0
        cfg.pad_to = 0
    preprocessor = model.from_config_dict(cfg)
    preprocessor.eval()
    return preprocessor.to(model.device)


class StreamingFeatureExtractor:
    """Turns an arbitrary-sized audio stream into log-mel frames.

    Frame ``f`` is centered on sample ``f * hop_length`` (STFT ``center=True``),
    so it can only be computed once ``f * hop_length + n_fft // 2`` samples
    have arrived.  ``push`` returns just the newly completed frames.
    """

    def __init__(self, preprocessor: torch.nn.Module, device: torch.device):
        self.preprocessor = preprocessor
        self.device = device
        featurizer = preprocessor.featurizer
        self.hop_length = int(featurizer.hop_length)
        self.half_window = int(featurizer.n_fft) // 2
        # Frames of left context needed so the first emitted frame never sees
        # the zero padding at the start of a segment.
        self.margin_frames = math.ceil(self.half_window / self.hop_length) + 1
        self.reset()

    def reset(self) -> None:
        self._audio = np.zeros(0, dtype=np.float32)
        self._audio_start = 0  # absolute index of self._audio[0]
        self.frames_emitted = 0  # absolute index of the next frame to emit
        self._flushed = False

    @torch.no_grad()
    def _compute(self, first: int, last: int, pad_right: int = 0) -> torch.Tensor:
        """Compute absolute frames [first, last] from the retained audio."""
        seg_start_frame = max(0, first - self.margin_frames)
        s0 = seg_start_frame * self.hop_length
        s1 = min(
            last * self.hop_length + self.half_window,
            self._audio_start + len(self._audio),
        )
        segment = self._audio[s0 - self._audio_start : s1 - self._audio_start]
        if pad_right:
            segment = np.concatenate([segment, np.zeros(pad_right, dtype=np.float32)])
        signal = torch.from_numpy(np.ascontiguousarray(segment)).unsqueeze(0).to(self.device)
        length = torch.tensor([signal.shape[1]], device=self.device)
        feats, _ = self.preprocessor(input_signal=signal, length=length)
        rel = first - seg_start_frame
        return feats[:, :, rel : rel + (last - first + 1)]

    def _trim(self) -> None:
        keep_from = max(0, (self.frames_emitted - self.margin_frames) * self.hop_length)
        drop = keep_from - self._audio_start
        if drop > 0:
            self._audio = self._audio[drop:]
            self._audio_start = keep_from

    def push(self, audio: np.ndarray) -> Optional[torch.Tensor]:
        """Append audio; return newly completed frames as [1, n_feats, n] or None."""
        if len(audio):
            self._audio = np.concatenate([self._audio, audio.astype(np.float32, copy=False)])
        total = self._audio_start + len(self._audio)
        last = (total - self.half_window) // self.hop_length
        first = self.frames_emitted
        if last < first:
            return None
        feats = self._compute(first, last)
        self.frames_emitted = last + 1
        self._trim()
        return feats

    def flush(self) -> Optional[torch.Tensor]:
        """Emit the trailing frames, zero-padding the end like offline preprocessing."""
        if self._flushed:
            return None
        self._flushed = True
        total = self._audio_start + len(self._audio)
        last = total // self.hop_length
        first = self.frames_emitted
        if last < first:
            return None
        feats = self._compute(first, last, pad_right=self.half_window)
        self.frames_emitted = last + 1
        self._trim()
        return feats
