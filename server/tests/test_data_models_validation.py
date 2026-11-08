"""
Unit tests for data model validation.
"""

import pytest
from datetime import datetime

from server.model_management.data_models import ModelConfig, ModelStatus, MemoryUsage, ModelType


class TestModelConfigValidation:
    """Test validation for ModelConfig data class."""
    
    def test_valid_model_config(self):
        """Test creating a valid ModelConfig."""
        config = ModelConfig(
            model_type=ModelType.WHISPER_TRANSCRIBER,
            model_name="openai/whisper-base",
            device="cuda",
            ttl_seconds=300,
            max_memory_mb=1024,
            priority=1,
            lazy_loading=True
        )
        
        assert config.model_type == ModelType.WHISPER_TRANSCRIBER
        assert config.model_name == "openai/whisper-base"
        assert config.device == "cuda"
        assert config.ttl_seconds == 300
        assert config.max_memory_mb == 1024
        assert config.priority == 1
        assert config.lazy_loading is True
        assert config.fallback_configs == []
    
    def test_invalid_model_type(self):
        """Test ModelConfig with invalid model_type."""
        with pytest.raises(ValueError, match="model_type must be a ModelType enum"):
            ModelConfig(
                model_type="invalid_type",
                model_name="test",
                device="cpu"
            )
    
    def test_empty_model_name(self):
        """Test ModelConfig with empty model_name."""
        with pytest.raises(ValueError, match="model_name must be a non-empty string"):
            ModelConfig(
                model_type=ModelType.WHISPER_TRANSCRIBER,
                model_name="",
                device="cpu"
            )
    
    def test_none_model_name(self):
        """Test ModelConfig with None model_name."""
        with pytest.raises(ValueError, match="model_name must be a non-empty string"):
            ModelConfig(
                model_type=ModelType.WHISPER_TRANSCRIBER,
                model_name=None,
                device="cpu"
            )
    
    def test_empty_device(self):
        """Test ModelConfig with empty device."""
        with pytest.raises(ValueError, match="device must be a non-empty string"):
            ModelConfig(
                model_type=ModelType.WHISPER_TRANSCRIBER,
                model_name="test",
                device=""
            )
    
    def test_negative_ttl_seconds(self):
        """Test ModelConfig with negative ttl_seconds."""
        with pytest.raises(ValueError, match="ttl_seconds must be a non-negative integer"):
            ModelConfig(
                model_type=ModelType.WHISPER_TRANSCRIBER,
                model_name="test",
                device="cpu",
                ttl_seconds=-100
            )
    
    def test_zero_max_memory_mb(self):
        """Test ModelConfig with zero max_memory_mb."""
        with pytest.raises(ValueError, match="max_memory_mb must be a positive integer"):
            ModelConfig(
                model_type=ModelType.WHISPER_TRANSCRIBER,
                model_name="test",
                device="cpu",
                max_memory_mb=0
            )
    
    def test_negative_max_memory_mb(self):
        """Test ModelConfig with negative max_memory_mb."""
        with pytest.raises(ValueError, match="max_memory_mb must be a positive integer"):
            ModelConfig(
                model_type=ModelType.WHISPER_TRANSCRIBER,
                model_name="test",
                device="cpu",
                max_memory_mb=-1024
            )
    
    def test_zero_priority(self):
        """Test ModelConfig with zero priority."""
        with pytest.raises(ValueError, match="priority must be a positive integer"):
            ModelConfig(
                model_type=ModelType.WHISPER_TRANSCRIBER,
                model_name="test",
                device="cpu",
                priority=0
            )
    
    def test_non_boolean_lazy_loading(self):
        """Test ModelConfig with non-boolean lazy_loading."""
        with pytest.raises(ValueError, match="lazy_loading must be a boolean"):
            ModelConfig(
                model_type=ModelType.WHISPER_TRANSCRIBER,
                model_name="test",
                device="cpu",
                lazy_loading="true"
            )


class TestModelStatusValidation:
    """Test validation for ModelStatus data class."""
    
    def test_valid_model_status(self):
        """Test creating a valid ModelStatus."""
        now = datetime.now()
        status = ModelStatus(
            model_id="whisper-1",
            model_type=ModelType.WHISPER_TRANSCRIBER,
            is_loaded=True,
            memory_usage_mb=512,
            last_access_time=now,
            load_time_ms=1500,
            access_count=10
        )
        
        assert status.model_id == "whisper-1"
        assert status.model_type == ModelType.WHISPER_TRANSCRIBER
        assert status.is_loaded is True
        assert status.memory_usage_mb == 512
        assert status.last_access_time == now
        assert status.load_time_ms == 1500
        assert status.access_count == 10
    
    def test_empty_model_id(self):
        """Test ModelStatus with empty model_id."""
        with pytest.raises(ValueError, match="model_id must be a non-empty string"):
            ModelStatus(
                model_id="",
                model_type=ModelType.WHISPER_TRANSCRIBER,
                is_loaded=True,
                memory_usage_mb=512,
                last_access_time=datetime.now(),
                load_time_ms=1500,
                access_count=10
            )
    
    def test_invalid_model_type(self):
        """Test ModelStatus with invalid model_type."""
        with pytest.raises(ValueError, match="model_type must be a ModelType enum"):
            ModelStatus(
                model_id="test-1",
                model_type="invalid",
                is_loaded=True,
                memory_usage_mb=512,
                last_access_time=datetime.now(),
                load_time_ms=1500,
                access_count=10
            )
    
    def test_non_boolean_is_loaded(self):
        """Test ModelStatus with non-boolean is_loaded."""
        with pytest.raises(ValueError, match="is_loaded must be a boolean"):
            ModelStatus(
                model_id="test-1",
                model_type=ModelType.WHISPER_TRANSCRIBER,
                is_loaded="true",
                memory_usage_mb=512,
                last_access_time=datetime.now(),
                load_time_ms=1500,
                access_count=10
            )
    
    def test_negative_memory_usage_mb(self):
        """Test ModelStatus with negative memory_usage_mb."""
        with pytest.raises(ValueError, match="memory_usage_mb must be a non-negative integer"):
            ModelStatus(
                model_id="test-1",
                model_type=ModelType.WHISPER_TRANSCRIBER,
                is_loaded=True,
                memory_usage_mb=-512,
                last_access_time=datetime.now(),
                load_time_ms=1500,
                access_count=10
            )
    
    def test_invalid_last_access_time(self):
        """Test ModelStatus with invalid last_access_time."""
        with pytest.raises(ValueError, match="last_access_time must be a datetime object"):
            ModelStatus(
                model_id="test-1",
                model_type=ModelType.WHISPER_TRANSCRIBER,
                is_loaded=True,
                memory_usage_mb=512,
                last_access_time="2023-01-01",
                load_time_ms=1500,
                access_count=10
            )
    
    def test_negative_load_time_ms(self):
        """Test ModelStatus with negative load_time_ms."""
        with pytest.raises(ValueError, match="load_time_ms must be a non-negative integer"):
            ModelStatus(
                model_id="test-1",
                model_type=ModelType.WHISPER_TRANSCRIBER,
                is_loaded=True,
                memory_usage_mb=512,
                last_access_time=datetime.now(),
                load_time_ms=-1500,
                access_count=10
            )
    
    def test_negative_access_count(self):
        """Test ModelStatus with negative access_count."""
        with pytest.raises(ValueError, match="access_count must be a non-negative integer"):
            ModelStatus(
                model_id="test-1",
                model_type=ModelType.WHISPER_TRANSCRIBER,
                is_loaded=True,
                memory_usage_mb=512,
                last_access_time=datetime.now(),
                load_time_ms=1500,
                access_count=-10
            )


class TestMemoryUsageValidation:
    """Test validation for MemoryUsage data class."""
    
    def test_valid_memory_usage(self):
        """Test creating a valid MemoryUsage."""
        memory = MemoryUsage(
            total_mb=8192,
            used_mb=4096,
            available_mb=4096,
            gpu_total_mb=4096,
            gpu_used_mb=2048
        )
        
        assert memory.total_mb == 8192
        assert memory.used_mb == 4096
        assert memory.available_mb == 4096
        assert memory.gpu_total_mb == 4096
        assert memory.gpu_used_mb == 2048
        assert memory.usage_percentage == 50.0
        assert memory.gpu_usage_percentage == 50.0
    
    def test_negative_total_mb(self):
        """Test MemoryUsage with negative total_mb."""
        with pytest.raises(ValueError, match="total_mb must be a non-negative integer"):
            MemoryUsage(
                total_mb=-8192,
                used_mb=4096,
                available_mb=4096
            )
    
    def test_negative_used_mb(self):
        """Test MemoryUsage with negative used_mb."""
        with pytest.raises(ValueError, match="used_mb must be a non-negative integer"):
            MemoryUsage(
                total_mb=8192,
                used_mb=-4096,
                available_mb=4096
            )
    
    def test_negative_available_mb(self):
        """Test MemoryUsage with negative available_mb."""
        with pytest.raises(ValueError, match="available_mb must be a non-negative integer"):
            MemoryUsage(
                total_mb=8192,
                used_mb=4096,
                available_mb=-4096
            )
    
    def test_negative_gpu_total_mb(self):
        """Test MemoryUsage with negative gpu_total_mb."""
        with pytest.raises(ValueError, match="gpu_total_mb must be a non-negative integer"):
            MemoryUsage(
                total_mb=8192,
                used_mb=4096,
                available_mb=4096,
                gpu_total_mb=-4096,
                gpu_used_mb=2048
            )
    
    def test_negative_gpu_used_mb(self):
        """Test MemoryUsage with negative gpu_used_mb."""
        with pytest.raises(ValueError, match="gpu_used_mb must be a non-negative integer"):
            MemoryUsage(
                total_mb=8192,
                used_mb=4096,
                available_mb=4096,
                gpu_total_mb=4096,
                gpu_used_mb=-2048
            )
    
    def test_used_mb_greater_than_total_mb(self):
        """Test MemoryUsage with used_mb greater than total_mb."""
        with pytest.raises(ValueError, match="used_mb cannot be greater than total_mb"):
            MemoryUsage(
                total_mb=4096,
                used_mb=8192,
                available_mb=0
            )
    
    def test_gpu_used_mb_greater_than_gpu_total_mb(self):
        """Test MemoryUsage with gpu_used_mb greater than gpu_total_mb."""
        with pytest.raises(ValueError, match="gpu_used_mb cannot be greater than gpu_total_mb"):
            MemoryUsage(
                total_mb=8192,
                used_mb=4096,
                available_mb=4096,
                gpu_total_mb=2048,
                gpu_used_mb=4096
            )
    
    def test_zero_total_memory_percentage(self):
        """Test MemoryUsage percentage calculation with zero total memory."""
        memory = MemoryUsage(
            total_mb=0,
            used_mb=0,
            available_mb=0,
            gpu_total_mb=0,
            gpu_used_mb=0
        )
        
        assert memory.usage_percentage == 0.0
        assert memory.gpu_usage_percentage == 0.0
    
    def test_memory_usage_percentage_calculation(self):
        """Test MemoryUsage percentage calculations."""
        memory = MemoryUsage(
            total_mb=1000,
            used_mb=250,
            available_mb=750,
            gpu_total_mb=2000,
            gpu_used_mb=500
        )
        
        assert memory.usage_percentage == 25.0
        assert memory.gpu_usage_percentage == 25.0