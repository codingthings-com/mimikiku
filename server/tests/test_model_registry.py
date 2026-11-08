"""
Unit tests for ModelRegistry class.
"""

import pytest
from unittest.mock import Mock

from model_management.model_registry import ModelRegistry
from model_management.data_models import ModelType, ModelConfig
from model_management.exceptions import ModelNotFoundError, ConfigurationError
from model_management.base_model import BaseModel


class MockModel(BaseModel):
    """Mock model for testing."""
    
    def __init__(self, model_id: str, config: ModelConfig):
        super().__init__(model_id, config)
    
    async def load(self) -> None:
        pass
    
    async def unload(self) -> None:
        pass
    
    def is_loaded(self) -> bool:
        return True
    
    def get_memory_usage(self):
        from model_management.data_models import MemoryUsage
        return MemoryUsage(total_mb=1024, used_mb=512, available_mb=512)


def mock_factory(model_id: str, config: ModelConfig) -> BaseModel:
    """Mock factory function for testing."""
    return MockModel(model_id, config)


def failing_factory(model_id: str, config: ModelConfig) -> BaseModel:
    """Mock factory that always fails."""
    raise RuntimeError("Factory failure")


class TestModelRegistry:
    """Test ModelRegistry class."""
    
    def test_registry_initialization(self):
        """Test ModelRegistry initialization."""
        registry = ModelRegistry()
        
        assert len(registry.get_registered_types()) == 0
        assert not registry.is_registered(ModelType.WHISPER_TRANSCRIBER)
    
    def test_register_model_type(self):
        """Test registering a model type."""
        registry = ModelRegistry()
        
        registry.register_model_type(ModelType.WHISPER_TRANSCRIBER, mock_factory)
        
        assert registry.is_registered(ModelType.WHISPER_TRANSCRIBER)
        assert ModelType.WHISPER_TRANSCRIBER in registry.get_registered_types()
    
    def test_register_duplicate_model_type(self):
        """Test registering a duplicate model type raises error."""
        registry = ModelRegistry()
        
        registry.register_model_type(ModelType.WHISPER_TRANSCRIBER, mock_factory)
        
        with pytest.raises(ConfigurationError, match="already registered"):
            registry.register_model_type(ModelType.WHISPER_TRANSCRIBER, mock_factory)
    
    def test_unregister_model_type(self):
        """Test unregistering a model type."""
        registry = ModelRegistry()
        
        # Register then unregister
        registry.register_model_type(ModelType.WHISPER_TRANSCRIBER, mock_factory)
        assert registry.is_registered(ModelType.WHISPER_TRANSCRIBER)
        
        registry.unregister_model_type(ModelType.WHISPER_TRANSCRIBER)
        assert not registry.is_registered(ModelType.WHISPER_TRANSCRIBER)
    
    def test_unregister_nonexistent_model_type(self):
        """Test unregistering a non-existent model type doesn't raise error."""
        registry = ModelRegistry()
        
        # Should not raise an error
        registry.unregister_model_type(ModelType.WHISPER_TRANSCRIBER)
    
    def test_create_model_success(self):
        """Test successful model creation."""
        registry = ModelRegistry()
        registry.register_model_type(ModelType.WHISPER_TRANSCRIBER, mock_factory)
        
        config = ModelConfig(
            model_type=ModelType.WHISPER_TRANSCRIBER,
            model_name="whisper-large-v3",
            device="cuda"
        )
        
        model = registry.create_model(ModelType.WHISPER_TRANSCRIBER, "test_model", config)
        
        assert isinstance(model, MockModel)
        assert model.model_id == "test_model"
        assert model.config == config
    
    def test_create_model_unregistered_type(self):
        """Test creating model with unregistered type raises error."""
        registry = ModelRegistry()
        
        config = ModelConfig(
            model_type=ModelType.WHISPER_TRANSCRIBER,
            model_name="whisper-large-v3",
            device="cuda"
        )
        
        with pytest.raises(ModelNotFoundError, match="not registered"):
            registry.create_model(ModelType.WHISPER_TRANSCRIBER, "test_model", config)
    
    def test_create_model_config_type_mismatch(self):
        """Test creating model with mismatched config type raises error."""
        registry = ModelRegistry()
        registry.register_model_type(ModelType.WHISPER_TRANSCRIBER, mock_factory)
        
        config = ModelConfig(
            model_type=ModelType.SPEAKER_DIARIZATION,  # Different type
            model_name="whisper-large-v3",
            device="cuda"
        )
        
        with pytest.raises(ConfigurationError, match="does not match"):
            registry.create_model(ModelType.WHISPER_TRANSCRIBER, "test_model", config)
    
    def test_create_model_factory_failure(self):
        """Test model creation when factory fails."""
        registry = ModelRegistry()
        registry.register_model_type(ModelType.WHISPER_TRANSCRIBER, failing_factory)
        
        config = ModelConfig(
            model_type=ModelType.WHISPER_TRANSCRIBER,
            model_name="whisper-large-v3",
            device="cuda"
        )
        
        with pytest.raises(ConfigurationError, match="Failed to create model"):
            registry.create_model(ModelType.WHISPER_TRANSCRIBER, "test_model", config)
    
    def test_fallback_configs(self):
        """Test fallback configuration management."""
        registry = ModelRegistry()
        
        fallback_config = ModelConfig(
            model_type=ModelType.WHISPER_TRANSCRIBER,
            model_name="whisper-small",
            device="cpu"
        )
        
        # Initially no fallbacks
        assert len(registry.get_fallback_configs(ModelType.WHISPER_TRANSCRIBER)) == 0
        
        # Register fallbacks
        registry.register_fallback_configs(ModelType.WHISPER_TRANSCRIBER, [fallback_config])
        
        fallbacks = registry.get_fallback_configs(ModelType.WHISPER_TRANSCRIBER)
        assert len(fallbacks) == 1
        assert fallbacks[0] == fallback_config
    
    def test_get_registered_types(self):
        """Test getting all registered types."""
        registry = ModelRegistry()
        
        # Initially empty
        assert len(registry.get_registered_types()) == 0
        
        # Register multiple types
        registry.register_model_type(ModelType.WHISPER_TRANSCRIBER, mock_factory)
        registry.register_model_type(ModelType.SPEAKER_DIARIZATION, mock_factory)
        
        registered_types = registry.get_registered_types()
        assert len(registered_types) == 2
        assert ModelType.WHISPER_TRANSCRIBER in registered_types
        assert ModelType.SPEAKER_DIARIZATION in registered_types