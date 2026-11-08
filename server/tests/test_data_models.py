"""
Unit tests for data models.
"""

import pytest
from datetime import datetime

from model_management.data_models import ModelType, ModelConfig, ModelStatus, MemoryUsage


class TestModelType:
    """Test ModelType enum."""
    
    def test_model_type_values(self):
        """Test that ModelType has expected values."""
        assert ModelType.WHISPER_TRANSCRIBER.value == "whisper_transcriber"
        assert ModelType.SPEAKER_DIARIZATION.value == "speaker_diarization"


class TestModelConfig:
    """Test ModelConfig dataclass."""
    
    def test_model_config_creation(self):
        """Test creating a ModelConfig with required fields."""
        config = ModelConfig(
            model_type=ModelType.WHISPER_TRANSCRIBER,
            model_name="whisper-large-v3",
            device="cuda"
        )
        
        assert config.model_type == ModelType.WHISPER_TRANSCRIBER
        assert config.model_name == "whisper-large-v3"
        assert config.device == "cuda"
        assert config.ttl_seconds == 300  # default
        assert config.max_memory_mb == 8192  # default
        assert config.fallback_configs == []  # default
        assert config.lazy_loading is True  # default
        assert config.priority == 1  # default
    
    def test_model_config_with_custom_values(self):
        """Test creating a ModelConfig with custom values."""
        fallback_config = ModelConfig(
            model_type=ModelType.WHISPER_TRANSCRIBER,
            model_name="whisper-small",
            device="cpu"
        )
        
        config = ModelConfig(
            model_type=ModelType.WHISPER_TRANSCRIBER,
            model_name="whisper-large-v3",
            device="cuda",
            ttl_seconds=600,
            max_memory_mb=4096,
            fallback_configs=[fallback_config],
            lazy_loading=False,
            priority=2
        )
        
        assert config.ttl_seconds == 600
        assert config.max_memory_mb == 4096
        assert len(config.fallback_configs) == 1
        assert config.fallback_configs[0] == fallback_config
        assert config.lazy_loading is False
        assert config.priority == 2


class TestModelStatus:
    """Test ModelStatus dataclass."""
    
    def test_model_status_creation(self):
        """Test creating a ModelStatus."""
        now = datetime.now()
        status = ModelStatus(
            model_id="test_model_1",
            model_type=ModelType.WHISPER_TRANSCRIBER,
            is_loaded=True,
            memory_usage_mb=1024,
            last_access_time=now,
            load_time_ms=5000,
            access_count=10
        )
        
        assert status.model_id == "test_model_1"
        assert status.model_type == ModelType.WHISPER_TRANSCRIBER
        assert status.is_loaded is True
        assert status.memory_usage_mb == 1024
        assert status.last_access_time == now
        assert status.load_time_ms == 5000
        assert status.access_count == 10


class TestMemoryUsage:
    """Test MemoryUsage dataclass."""
    
    def test_memory_usage_creation(self):
        """Test creating a MemoryUsage."""
        memory = MemoryUsage(
            total_mb=8192,
            used_mb=4096,
            available_mb=4096,
            gpu_total_mb=12288,
            gpu_used_mb=6144
        )
        
        assert memory.total_mb == 8192
        assert memory.used_mb == 4096
        assert memory.available_mb == 4096
        assert memory.gpu_total_mb == 12288
        assert memory.gpu_used_mb == 6144
    
    def test_memory_usage_defaults(self):
        """Test MemoryUsage with default GPU values."""
        memory = MemoryUsage(
            total_mb=8192,
            used_mb=4096,
            available_mb=4096
        )
        
        assert memory.gpu_total_mb == 0
        assert memory.gpu_used_mb == 0
    
    def test_usage_percentage(self):
        """Test usage percentage calculation."""
        memory = MemoryUsage(
            total_mb=8192,
            used_mb=4096,
            available_mb=4096
        )
        
        assert memory.usage_percentage == 50.0
    
    def test_usage_percentage_zero_total(self):
        """Test usage percentage with zero total memory."""
        memory = MemoryUsage(
            total_mb=0,
            used_mb=0,
            available_mb=0
        )
        
        assert memory.usage_percentage == 0.0
    
    def test_gpu_usage_percentage(self):
        """Test GPU usage percentage calculation."""
        memory = MemoryUsage(
            total_mb=8192,
            used_mb=4096,
            available_mb=4096,
            gpu_total_mb=12288,
            gpu_used_mb=3072
        )
        
        assert memory.gpu_usage_percentage == 25.0
    
    def test_gpu_usage_percentage_zero_total(self):
        """Test GPU usage percentage with zero total GPU memory."""
        memory = MemoryUsage(
            total_mb=8192,
            used_mb=4096,
            available_mb=4096,
            gpu_total_mb=0,
            gpu_used_mb=0
        )
        
        assert memory.gpu_usage_percentage == 0.0