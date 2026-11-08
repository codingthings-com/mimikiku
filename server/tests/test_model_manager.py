"""
Unit tests for ModelManager class.
"""

import pytest
import asyncio
from datetime import datetime, timedelta
from unittest.mock import Mock, AsyncMock

from model_management.model_manager import ModelManager
from model_management.model_registry import ModelRegistry
from model_management.data_models import ModelType, ModelConfig, MemoryUsage
from model_management.base_model import BaseModel
from model_management.exceptions import ModelLoadError


class MockModel(BaseModel):
    """Mock model for testing."""
    
    def __init__(self, model_id: str, config: ModelConfig):
        super().__init__(model_id, config)
        self._loaded = False
        self.load_called = False
        self.unload_called = False
        self.load_should_fail = False
    
    async def load(self) -> None:
        self.load_called = True
        if self.load_should_fail:
            raise RuntimeError("Load failed")
        self._loaded = True
        self._is_loaded = True
    
    async def unload(self) -> None:
        self.unload_called = True
        self._loaded = False
        self._is_loaded = False
    
    def is_loaded(self) -> bool:
        return self._loaded
    
    def get_memory_usage(self) -> MemoryUsage:
        return MemoryUsage(total_mb=1024, used_mb=512, available_mb=512)


def mock_factory(model_id: str, config: ModelConfig) -> BaseModel:
    """Mock factory function for testing."""
    return MockModel(model_id, config)


class TestModelManager:
    """Test ModelManager class."""
    
    @pytest.fixture
    def registry(self):
        """Create a mock registry for testing."""
        registry = ModelRegistry()
        registry.register_model_type(ModelType.WHISPER_TRANSCRIBER, mock_factory)
        return registry
    
    @pytest.fixture
    def manager(self, registry):
        """Create a model manager for testing."""
        return ModelManager(registry)
    
    @pytest.mark.asyncio
    async def test_manager_initialization(self, manager):
        """Test ModelManager initialization."""
        assert not manager._running
        assert manager._cleanup_task is None
        assert len(manager._loaded_models) == 0
        assert len(manager._model_configs) == 0
    
    @pytest.mark.asyncio
    async def test_get_model_new_model(self, manager):
        """Test getting a new model."""
        config = ModelConfig(
            model_type=ModelType.WHISPER_TRANSCRIBER,
            model_name="whisper-large-v3",
            device="cuda"
        )
        
        model = await manager.get_model(ModelType.WHISPER_TRANSCRIBER, config)
        
        assert isinstance(model, MockModel)
        assert model.is_loaded()
        assert model.load_called
        assert model.get_access_count() == 1
        assert model.get_last_access_time() is not None
    
    @pytest.mark.asyncio
    async def test_get_model_existing_model(self, manager):
        """Test getting an existing model."""
        config = ModelConfig(
            model_type=ModelType.WHISPER_TRANSCRIBER,
            model_name="whisper-large-v3",
            device="cuda"
        )
        
        # Get model first time
        model1 = await manager.get_model(ModelType.WHISPER_TRANSCRIBER, config)
        access_count_1 = model1.get_access_count()
        
        # Get same model again
        model2 = await manager.get_model(ModelType.WHISPER_TRANSCRIBER, config)
        
        assert model1 is model2  # Should be the same instance
        assert model2.get_access_count() == access_count_1 + 1
    
    @pytest.mark.asyncio
    async def test_release_model(self, manager):
        """Test releasing a model."""
        config = ModelConfig(
            model_type=ModelType.WHISPER_TRANSCRIBER,
            model_name="whisper-large-v3",
            device="cuda"
        )
        
        # Get and then release model
        model = await manager.get_model(ModelType.WHISPER_TRANSCRIBER, config)
        model_id = model.model_id
        
        await manager.release_model(model_id)
        
        assert model.unload_called
        assert model_id not in manager._loaded_models
        assert model_id not in manager._model_configs
    
    @pytest.mark.asyncio
    async def test_cleanup_idle_models(self, manager):
        """Test cleanup of idle models."""
        config = ModelConfig(
            model_type=ModelType.WHISPER_TRANSCRIBER,
            model_name="whisper-large-v3",
            device="cuda",
            ttl_seconds=1  # Very short TTL for testing
        )
        
        # Get model
        model = await manager.get_model(ModelType.WHISPER_TRANSCRIBER, config)
        model_id = model.model_id
        
        # Manually set last access time to past
        model._last_access_time = datetime.now() - timedelta(seconds=2)
        
        # Run cleanup
        await manager.cleanup_idle_models()
        
        assert model.unload_called
        assert model_id not in manager._loaded_models
    
    @pytest.mark.asyncio
    async def test_update_configuration(self, manager):
        """Test updating manager configuration."""
        original_interval = manager._cleanup_interval
        
        await manager.update_configuration({'cleanup_interval': 120})
        
        assert manager._cleanup_interval == 120
        assert manager._cleanup_interval != original_interval
    
    def test_generate_model_id(self, manager):
        """Test model ID generation."""
        config = ModelConfig(
            model_type=ModelType.WHISPER_TRANSCRIBER,
            model_name="whisper-large-v3",
            device="cuda"
        )
        
        model_id = manager._generate_model_id(ModelType.WHISPER_TRANSCRIBER, config)
        
        assert isinstance(model_id, str)
        assert "whisper_transcriber" in model_id
        assert "whisper-large-v3" in model_id
        assert "cuda" in model_id
        
        # Same config should generate same ID
        model_id2 = manager._generate_model_id(ModelType.WHISPER_TRANSCRIBER, config)
        assert model_id == model_id2
        
        # Different config should generate different ID
        config2 = ModelConfig(
            model_type=ModelType.WHISPER_TRANSCRIBER,
            model_name="whisper-small",
            device="cuda"
        )
        model_id3 = manager._generate_model_id(ModelType.WHISPER_TRANSCRIBER, config2)
        assert model_id != model_id3