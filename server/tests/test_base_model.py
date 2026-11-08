"""
Unit tests for BaseModel abstract class.
"""

import pytest
from datetime import datetime
from unittest.mock import Mock

from model_management.base_model import BaseModel
from model_management.data_models import MemoryUsage


class MockModel(BaseModel):
    """Mock implementation of BaseModel for testing."""
    
    def __init__(self, model_id: str, config: Mock):
        super().__init__(model_id, config)
        self._mock_loaded = False
        self._mock_memory_usage = MemoryUsage(
            total_mb=8192,
            used_mb=1024,
            available_mb=7168
        )
    
    async def load(self) -> None:
        """Mock load implementation."""
        self._mock_loaded = True
        self._is_loaded = True
    
    async def unload(self) -> None:
        """Mock unload implementation."""
        self._mock_loaded = False
        self._is_loaded = False
    
    def is_loaded(self) -> bool:
        """Mock is_loaded implementation."""
        return self._mock_loaded
    
    def get_memory_usage(self) -> MemoryUsage:
        """Mock get_memory_usage implementation."""
        return self._mock_memory_usage


class TestBaseModel:
    """Test BaseModel abstract class."""
    
    def test_base_model_initialization(self):
        """Test BaseModel initialization."""
        config = Mock()
        model = MockModel("test_model", config)
        
        assert model.model_id == "test_model"
        assert model.config == config
        assert model._is_loaded is False
        assert model._last_access_time is None
        assert model._load_time_ms == 0
        assert model._access_count == 0
    
    @pytest.mark.asyncio
    async def test_load_unload_cycle(self):
        """Test load and unload operations."""
        config = Mock()
        model = MockModel("test_model", config)
        
        # Initially not loaded
        assert not model.is_loaded()
        
        # Load the model
        await model.load()
        assert model.is_loaded()
        
        # Unload the model
        await model.unload()
        assert not model.is_loaded()
    
    def test_access_time_tracking(self):
        """Test access time tracking functionality."""
        config = Mock()
        model = MockModel("test_model", config)
        
        # Initially no access time
        assert model.get_last_access_time() is None
        assert model.get_access_count() == 0
        
        # Update access time
        before_update = datetime.now()
        model.update_access_time()
        after_update = datetime.now()
        
        access_time = model.get_last_access_time()
        assert access_time is not None
        assert before_update <= access_time <= after_update
        assert model.get_access_count() == 1
        
        # Update again
        model.update_access_time()
        assert model.get_access_count() == 2
    
    def test_load_time_tracking(self):
        """Test load time tracking functionality."""
        config = Mock()
        model = MockModel("test_model", config)
        
        # Initially no load time
        assert model.get_load_time_ms() == 0
        
        # Set load time
        model.set_load_time_ms(5000)
        assert model.get_load_time_ms() == 5000
    
    def test_memory_usage(self):
        """Test memory usage reporting."""
        config = Mock()
        model = MockModel("test_model", config)
        
        memory_usage = model.get_memory_usage()
        assert isinstance(memory_usage, MemoryUsage)
        assert memory_usage.total_mb == 8192
        assert memory_usage.used_mb == 1024
        assert memory_usage.available_mb == 7168
    
    def test_abstract_methods_must_be_implemented(self):
        """Test that BaseModel cannot be instantiated directly."""
        config = Mock()
        
        with pytest.raises(TypeError):
            BaseModel("test_model", config)