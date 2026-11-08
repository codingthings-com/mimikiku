"""
Data models for the model management system.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import List, Optional

from .constants import (
    DEFAULT_TTL_SECONDS,
    DEFAULT_MAX_MEMORY_MB,
    DEFAULT_PRIORITY,
    DEFAULT_LAZY_LOADING,
)


class ModelType(Enum):
    """Enumeration of supported model types."""

    WHISPER_TRANSCRIBER = "whisper_transcriber"
    SPEAKER_DIARIZATION = "speaker_diarization"
    NEMOTRON_ASR = "nemotron_asr"
    SORTFORMER_DIARIZATION = "sortformer_diarization"


@dataclass
class ModelConfig:
    """Configuration for a model instance."""

    model_type: ModelType
    model_name: str
    device: str
    ttl_seconds: int = DEFAULT_TTL_SECONDS
    max_memory_mb: int = DEFAULT_MAX_MEMORY_MB
    fallback_configs: List["ModelConfig"] = None
    lazy_loading: bool = DEFAULT_LAZY_LOADING
    priority: int = DEFAULT_PRIORITY  # Higher number = higher priority

    def __post_init__(self):
        if self.fallback_configs is None:
            self.fallback_configs = []

        # Validation
        if not isinstance(self.model_type, ModelType):
            raise ValueError(
                f"model_type must be a ModelType enum, got {type(self.model_type)}"
            )

        if not self.model_name or not isinstance(self.model_name, str):
            raise ValueError("model_name must be a non-empty string")

        if not self.device or not isinstance(self.device, str):
            raise ValueError("device must be a non-empty string")

        if not isinstance(self.ttl_seconds, int) or self.ttl_seconds < 0:
            raise ValueError("ttl_seconds must be a non-negative integer")

        if not isinstance(self.max_memory_mb, int) or self.max_memory_mb <= 0:
            raise ValueError("max_memory_mb must be a positive integer")

        if not isinstance(self.priority, int) or self.priority < 1:
            raise ValueError("priority must be a positive integer")

        if not isinstance(self.lazy_loading, bool):
            raise ValueError("lazy_loading must be a boolean")


@dataclass
class ModelStatus:
    """Status information for a loaded model."""

    model_id: str
    model_type: ModelType
    is_loaded: bool
    memory_usage_mb: int
    last_access_time: datetime
    load_time_ms: int
    access_count: int

    def __post_init__(self):
        # Validation
        if not self.model_id or not isinstance(self.model_id, str):
            raise ValueError("model_id must be a non-empty string")

        if not isinstance(self.model_type, ModelType):
            raise ValueError(
                f"model_type must be a ModelType enum, got {type(self.model_type)}"
            )

        if not isinstance(self.is_loaded, bool):
            raise ValueError("is_loaded must be a boolean")

        if not isinstance(self.memory_usage_mb, int) or self.memory_usage_mb < 0:
            raise ValueError("memory_usage_mb must be a non-negative integer")

        if not isinstance(self.last_access_time, datetime):
            raise ValueError("last_access_time must be a datetime object")

        if not isinstance(self.load_time_ms, int) or self.load_time_ms < 0:
            raise ValueError("load_time_ms must be a non-negative integer")

        if not isinstance(self.access_count, int) or self.access_count < 0:
            raise ValueError("access_count must be a non-negative integer")


@dataclass
class MemoryUsage:
    """System memory usage information."""

    total_mb: int
    used_mb: int
    available_mb: int
    gpu_total_mb: int = 0
    gpu_used_mb: int = 0

    def __post_init__(self):
        # Validation
        if not isinstance(self.total_mb, int) or self.total_mb < 0:
            raise ValueError("total_mb must be a non-negative integer")

        if not isinstance(self.used_mb, int) or self.used_mb < 0:
            raise ValueError("used_mb must be a non-negative integer")

        if not isinstance(self.available_mb, int) or self.available_mb < 0:
            raise ValueError("available_mb must be a non-negative integer")

        if not isinstance(self.gpu_total_mb, int) or self.gpu_total_mb < 0:
            raise ValueError("gpu_total_mb must be a non-negative integer")

        if not isinstance(self.gpu_used_mb, int) or self.gpu_used_mb < 0:
            raise ValueError("gpu_used_mb must be a non-negative integer")

        # Logical validation
        if self.used_mb > self.total_mb:
            raise ValueError("used_mb cannot be greater than total_mb")

        if self.gpu_used_mb > self.gpu_total_mb:
            raise ValueError("gpu_used_mb cannot be greater than gpu_total_mb")

    @property
    def usage_percentage(self) -> float:
        """Calculate memory usage percentage."""
        if self.total_mb == 0:
            return 0.0
        return (self.used_mb / self.total_mb) * 100.0

    @property
    def gpu_usage_percentage(self) -> float:
        """Calculate GPU memory usage percentage."""
        if self.gpu_total_mb == 0:
            return 0.0
        return (self.gpu_used_mb / self.gpu_total_mb) * 100.0
