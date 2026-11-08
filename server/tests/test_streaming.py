"""Tests for streaming feature extraction, word/speaker alignment and endpointing."""

import os
import sys
from types import SimpleNamespace

import numpy as np
import pytest
import torch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from streaming_audio import StreamingFeatureExtractor  # noqa: E402
from transcriber import (  # noqa: E402
    DiarizationTrack,
    EndpointConfig,
    StreamingASRSession,
    Word,
    assign_speakers,
)


# ---------------------------------------------------------------------------
# StreamingFeatureExtractor
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def preprocessor():
    from nemo.collections.asr.modules import AudioToMelSpectrogramPreprocessor

    pre = AudioToMelSpectrogramPreprocessor(
        sample_rate=16000,
        normalize="NA",
        window_size=0.025,
        window_stride=0.01,
        features=128,
        n_fft=512,
        dither=0.0,
        pad_to=0,
    )
    return pre.eval()


def test_streaming_features_match_offline(preprocessor):
    rng = np.random.default_rng(0)
    audio = (rng.standard_normal(16000 * 3) * 0.1).astype(np.float32)
    with torch.no_grad():
        full, _ = preprocessor(
            input_signal=torch.from_numpy(audio).unsqueeze(0),
            length=torch.tensor([len(audio)]),
        )

    extractor = StreamingFeatureExtractor(preprocessor, torch.device("cpu"))
    parts, i = [], 0
    while i < len(audio):
        n = int(rng.integers(50, 4000))
        out = extractor.push(audio[i : i + n])
        if out is not None:
            parts.append(out)
        i += n
    parts.append(extractor.flush())
    streamed = torch.cat(parts, dim=2)

    assert streamed.shape == full.shape
    # Frames whose window reaches past the end of the audio depend on how the
    # tail is padded; every other frame must match offline features.
    diff = (streamed - full)[:, :, :-2].abs().max().item()
    assert diff < 1e-3


def test_streaming_features_bounded_memory(preprocessor):
    extractor = StreamingFeatureExtractor(preprocessor, torch.device("cpu"))
    for _ in range(50):
        extractor.push(np.zeros(2048, dtype=np.float32))
    assert len(extractor._audio) < 2048 + 1000
    assert extractor.frames_emitted == (50 * 2048 - 256) // 160 + 1


# ---------------------------------------------------------------------------
# DiarizationTrack / assign_speakers
# ---------------------------------------------------------------------------


def _track(speaker_per_frame, n_spk=4):
    track = DiarizationTrack(n_spk)
    probs = np.zeros((len(speaker_per_frame), n_spk), dtype=np.float32)
    for f, spk in enumerate(speaker_per_frame):
        if spk is not None:
            probs[f, spk] = 0.9
    track.extend(0, probs)
    return track


def test_track_grows_and_clips():
    track = DiarizationTrack(2)
    track.extend(0, np.ones((300, 2), dtype=np.float32))
    track.extend(300, np.zeros((10, 2), dtype=np.float32))
    assert track.frames_available == 310
    assert track.window(305, 400).shape == (5, 2)
    assert track.window(400, 500).shape == (0, 2)


def test_assign_speakers_follows_frames():
    track = _track([0] * 20 + [1] * 20)
    words = [Word([1], 2, 5), Word([2], 8, 12), Word([3], 24, 28), Word([4], 32, 36)]
    assert assign_speakers(words, track) == [0, 0, 1, 1]


def test_assign_speakers_fills_silent_words_from_neighbours():
    track = _track([1] * 10 + [None] * 10 + [1] * 10)
    words = [Word([1], 2, 4), Word([2], 13, 16), Word([3], 24, 26)]
    assert assign_speakers(words, track) == [1, 1, 1]


def test_assign_speakers_absorbs_flicker():
    track = _track([0] * 20 + [2] * 3 + [0] * 20)
    words = [Word([1], 5, 10), Word([2], 21, 21), Word([3], 30, 35)]
    assert assign_speakers(words, track) == [0, 0, 0]


def test_assign_speakers_uses_previous_speaker_as_left_context():
    track = _track([2] * 3 + [0] * 30)
    words = [Word([1], 1, 1), Word([2], 10, 20)]
    assert assign_speakers(words, track, fallback=0) == [0, 0]
    assert assign_speakers(words, None, fallback=3) == [3, 3]


# ---------------------------------------------------------------------------
# StreamingASRSession endpointing
# ---------------------------------------------------------------------------


class FakeTokenizer:
    """Each token id maps to a piece; '▁' marks a word start."""

    def __init__(self, pieces):
        self.pieces = pieces

    def ids_to_tokens(self, ids):
        return [self.pieces[i] for i in ids]

    def ids_to_text(self, ids):
        return "".join(self.pieces[i] for i in ids).replace("▁", " ").strip()


PIECES = ["▁Hello", "▁world", ".", "▁How", "▁are", "▁you", "?", "▁fine", "▁th", "anks"]


def _session(endpoint=None):
    streaming_cfg = SimpleNamespace(
        chunk_size=[105, 112],
        pre_encode_cache_size=[0, 9],
        drop_extra_pre_encoded=2,
        valid_out_len=14,
    )
    model = SimpleNamespace(
        encoder=SimpleNamespace(
            streaming_cfg=streaming_cfg,
            get_initial_cache_state=lambda batch_size, device: (None, None, None),
        ),
        tokenizer=FakeTokenizer(PIECES),
    )
    session = StreamingASRSession.__new__(StreamingASRSession)
    session.model = model
    session.preprocessor = None
    session.device = torch.device("cpu")
    session.endpoint = endpoint or EndpointConfig()
    # Only the endpointing state from __post_init__ (no model or preprocessor).
    session.words, session.commit_upto, session.last_speaker = [], 0, -1
    session.encoded_frames = 0
    return session


def _emit(session, ids, frames, advance_to):
    session._add_tokens(ids, frames)
    session.encoded_frames = advance_to


def test_commits_complete_sentences_only():
    s = _session()
    _emit(s, [0, 1, 2, 3], [1, 3, 4, 10], advance_to=14)  # "Hello world. How"
    segments = s.pop_segments(None)
    assert [seg[1] for seg in segments] == ["Hello world."]
    assert [w.text for w in s.words] == ["How"]


def test_does_not_commit_trailing_sentence_end_until_next_word():
    s = _session()
    _emit(s, [0, 1, 2], [1, 3, 4], advance_to=14)
    assert s.pop_segments(None) == []


def test_word_pieces_join_across_steps():
    s = _session()
    _emit(s, [7, 8], [1, 12], advance_to=14)  # "fine th"
    _emit(s, [9], [15], advance_to=28)  # "anks"
    assert [w.text for w in s.words] == ["fine", "thanks"]
    assert s.words[1].start_frame == 12 and s.words[1].end_frame == 15


def test_pause_commits_everything():
    s = _session(EndpointConfig(pause_frames=10))
    _emit(s, [3, 4, 5], [1, 2, 3], advance_to=14)
    assert s.pop_segments(None) == [(-1, "How are you", 0.08, 0.32)]


def test_waits_for_diarization_then_splits_by_speaker():
    s = _session(EndpointConfig(diar_wait_frames=100))
    track = DiarizationTrack()
    _emit(s, [0, 1, 2, 3, 4, 5, 6, 7], [1, 3, 4, 20, 22, 24, 25, 40], advance_to=42)
    assert s.pop_segments(track) == []  # no diarization frames yet

    probs = np.zeros((40, 4), dtype=np.float32)
    probs[:12, 0] = 0.9
    probs[12:, 1] = 0.9
    track.extend(0, probs)
    segments = s.pop_segments(track)
    assert [(spk, text) for spk, text, _, _ in segments] == [
        (0, "Hello world."),
        (1, "How are you?"),
    ]
    assert s.last_speaker == 1


def test_diarization_wait_times_out():
    s = _session(EndpointConfig(diar_wait_frames=10, pause_frames=25))
    _emit(s, [0, 1, 2, 3], [1, 3, 4, 10], advance_to=30)  # no pause yet
    segments = s.pop_segments(DiarizationTrack())
    assert [seg[1] for seg in segments] == ["Hello world."]


def test_session_speaker_settings_reach_assign_speakers(monkeypatch):
    import transcriber

    seen = {}

    def fake_assign(words, track, fallback=-1, min_activity=None, min_run_frames=None):
        seen.update(min_activity=min_activity, min_run_frames=min_run_frames)
        return [0] * len(words)

    monkeypatch.setattr(transcriber, "assign_speakers", fake_assign)
    s = _session(EndpointConfig(min_activity=0.55, min_run_frames=3))
    _emit(s, [0, 1, 2, 3], [1, 3, 4, 10], advance_to=14)
    assert s.pop_segments(None)
    assert seen == {"min_activity": 0.55, "min_run_frames": 3}
