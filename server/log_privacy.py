"""Transcript text stays out of the logs unless LOG_TRANSCRIPTS=1.

Logs reach anyone with access to the server or to Loki, so by default they
carry only speakers, timings and word counts.
"""

import os


def enabled() -> bool:
    return os.environ.get("LOG_TRANSCRIPTS", "").lower() in ("1", "true", "yes")


def segment(speaker: int, text: str, start: float, end: float) -> str:
    """One finalized segment for the log: the text only when enabled, else a word count."""
    said = f'"{text}"' if enabled() else f"{len(text.split())} words"
    return f"Speaker {speaker} ({start:.2f}s-{end:.2f}s): {said}"
