"""
Unit tests for ModelCache class.
"""

import asyncio
import pytest
import pytest_asyncio
from datetime import datetime, timedelta
from unittest.mock import Mock, AsyncMock, patch

from server.model_management.model_cache import ModelCache, CacheEntry
from server.model_management.base_model import BaseModel
from server.model_management.data_models import ModelConfig, ModelType, MemoryUsage


class MockModel(BaseModel):
    """Mock model for testing."""
    
    def __init__(self, model_id: str, config: ModelConfig):
        super().__init__(model_id, config)
        self._loaded = False
        self._memory_usage = MemoryUsage(
            total_mb=1000,
            used_mb=100,
            available_mb=900
        )
    
    async def load(self) -> None:
        """Mock load implementation."""
        self._loaded = True
        self._is_loaded = True
    
    async def unload(self) -> None:
        """Mock unload implementation."""
        self._loaded = False
        self._is_loaded = False
    
    def is_loaded(self) -> bool:
        """Mock is_loaded implementation."""
        return self._loaded
    
    def get_memory_usage(self) -> MemoryUsage:
        """Mock get_memory_usage implementation."""
        return self._memory_usage


@pytest.fixture
def mock_config():
    """Create a mock model configuration."""
    return ModelConfig(
        model_type=ModelType.WHISPER_TRANSCRIBER,
        model_name="test_model",
        device="cpu",
        ttl_seconds=300,
        max_memory_mb=1024,
        priority=1
    )


@pytest.fixture
def mock_model(mock_config):
    """Create a mock model."""
    model = MockModel("test_model_id", mock_config)
    return model


@pytest_asyncio.fixture
async def cache():
    """Create a model cache for testing."""
    cache = ModelCache(max_capacity=3, cleanup_interval=1)
    await cache.start()
    try:
        yield cache
    finally:
        await cache.stop()


class TestCacheEntry:
    """Test CacheEntry functionality."""
    
    def test_cache_entry_creation(self, mock_model, mock_config):
        """Test CacheEntry creation."""
        now = datetime.now()
        entry = CacheEntry(
            model=mock_model,
            config=mock_config,
            created_at=now,
            last_accessed=now
        )
        
        assert entry.model == mock_model
        assert entry.config == mock_config
        assert entry.created_at == now
        assert entry.last_accessed == now
        assert entry.access_count == 0
    
    def test_is_expired_no_ttl(self, mock_model, mock_config):
        """Test expiration check with no TTL."""
        mock_config.ttl_seconds = 0  # No expiration
        entry = CacheEntry(
            model=mock_model,
            config=mock_config,
            created_at=datetime.now() - timedelta(hours=1),
            last_accessed=datetime.now()
        )
        
        assert not entry.is_expired()
    
    def test_is_expired_within_ttl(self, mock_model, mock_config):
        """Test expiration check within TTL."""
        mock_config.ttl_seconds = 300  # 5 minutes
        entry = CacheEntry(
            model=mock_model,
            config=mock_config,
            created_at=datetime.now() - timedelta(minutes=2),
            last_accessed=datetime.now()
        )
        
        assert not entry.is_expired()
    
    def test_is_expired_beyond_ttl(self, mock_model, mock_config):
        """Test expiration check beyond TTL."""
        mock_config.ttl_seconds = 300  # 5 minutes
        entry = CacheEntry(
            model=mock_model,
            config=mock_config,
            created_at=datetime.now() - timedelta(minutes=10),
            last_accessed=datetime.now()
        )
        
        assert entry.is_expired()
    
    def test_update_access(self, mock_model, mock_config):
        """Test access time update."""
        entry = CacheEntry(
            model=mock_model,
            config=mock_config,
            created_at=datetime.now(),
            last_accessed=datetime.now() - timedelta(minutes=1)
        )
        
        old_access_time = entry.last_accessed
        old_count = entry.access_count
        
        entry.update_access()
        
        assert entry.last_accessed > old_access_time
        assert entry.access_count == old_count + 1


class TestModelCache:
    """Test ModelCache functionality."""
    
    @pytest.mark.asyncio
    async def test_cache_initialization(self):
        """Test cache initialization."""
        cache = ModelCache(max_capacity=5, cleanup_interval=30)
        
        assert cache._max_capacity == 5
        assert cache._cleanup_interval == 30
        assert cache.size() == 0
        assert not cache._running
    
    @pytest.mark.asyncio
    async def test_cache_start_stop(self):
        """Test cache start and stop."""
        cache = ModelCache()
        
        # Test start
        await cache.start()
        assert cache._running
        assert cache._cleanup_task is not None
        
        # Test stop
        await cache.stop()
        assert not cache._running
        assert cache.size() == 0
    
    @pytest.mark.asyncio
    async def test_put_and_get(self, cache, mock_model, mock_config):
        """Test putting and getting models from cache."""
        await mock_model.load()
        
        # Put model in cache
        await cache.put("test_id", mock_model, mock_config)
        assert cache.size() == 1
        assert cache.contains("test_id")
        
        # Get model from cache
        retrieved_model = await cache.get("test_id")
        assert retrieved_model == mock_model
        assert retrieved_model.is_loaded()
    
    @pytest.mark.asyncio
    async def test_get_nonexistent(self, cache):
        """Test getting non-existent model."""
        result = await cache.get("nonexistent")
        assert result is None
    
    @pytest.mark.asyncio
    async def test_get_expired_model(self, cache, mock_model, mock_config):
        """Test getting expired model."""
        # Set very short TTL
        mock_config.ttl_seconds = 1
        await mock_model.load()
        
        # Put model in cache
        await cache.put("test_id", mock_model, mock_config)
        
        # Wait for expiration
        await asyncio.sleep(1.1)
        
        # Try to get expired model
        result = await cache.get("test_id")
        assert result is None
        assert not cache.contains("test_id")
    
    @pytest.mark.asyncio
    async def test_get_unloaded_model(self, cache, mock_model, mock_config):
        """Test getting model that became unloaded."""
        await mock_model.load()
        
        # Put model in cache
        await cache.put("test_id", mock_model, mock_config)
        
        # Unload the model
        await mock_model.unload()
        
        # Try to get unloaded model
        result = await cache.get("test_id")
        assert result is None
        assert not cache.contains("test_id")
    
    @pytest.mark.asyncio
    async def test_remove(self, cache, mock_model, mock_config):
        """Test removing model from cache."""
        await mock_model.load()
        
        # Put model in cache
        await cache.put("test_id", mock_model, mock_config)
        assert cache.contains("test_id")
        
        # Remove model
        removed = await cache.remove("test_id")
        assert removed
        assert not cache.contains("test_id")
        assert cache.size() == 0
        assert not mock_model.is_loaded()  # Should be unloaded
    
    @pytest.mark.asyncio
    async def test_remove_nonexistent(self, cache):
        """Test removing non-existent model."""
        removed = await cache.remove("nonexistent")
        assert not removed
    
    @pytest.mark.asyncio
    async def test_clear(self, cache, mock_config):
        """Test clearing all models from cache."""
        # Add multiple models
        models = []
        for i in range(3):
            model = MockModel(f"model_{i}", mock_config)
            await model.load()
            await cache.put(f"test_id_{i}", model, mock_config)
            models.append(model)
        
        assert cache.size() == 3
        
        # Clear cache
        await cache.clear()
        
        assert cache.size() == 0
        for model in models:
            assert not model.is_loaded()
    
    @pytest.mark.asyncio
    async def test_lru_eviction(self, cache, mock_config):
        """Test LRU eviction when capacity is reached."""
        # Fill cache to capacity (3)
        models = []
        for i in range(3):
            model = MockModel(f"model_{i}", mock_config)
            await model.load()
            await cache.put(f"test_id_{i}", model, mock_config)
            models.append(model)
        
        assert cache.size() == 3
        
        # Access first model to make it more recent
        await cache.get("test_id_0")
        
        # Add one more model (should evict LRU, which is test_id_1)
        new_model = MockModel("new_model", mock_config)
        await new_model.load()
        await cache.put("new_id", new_model, mock_config)
        
        assert cache.size() == 3
        assert cache.contains("test_id_0")  # Should still be there (recently accessed)
        assert not cache.contains("test_id_1")  # Should be evicted (LRU)
        assert cache.contains("test_id_2")  # Should still be there
        assert cache.contains("new_id")  # Should be added
        
        # Check that evicted model was unloaded
        assert not models[1].is_loaded()
    
    @pytest.mark.asyncio
    async def test_manual_lru_eviction(self, cache, mock_config):
        """Test manual LRU eviction."""
        # Add models
        models = []
        for i in range(3):
            model = MockModel(f"model_{i}", mock_config)
            await model.load()
            await cache.put(f"test_id_{i}", model, mock_config)
            models.append(model)
        
        # Evict 2 LRU models
        evicted_count = await cache.evict_lru(2)
        
        assert evicted_count == 2
        assert cache.size() == 1
        assert cache.contains("test_id_2")  # Most recent should remain
        assert not models[0].is_loaded()
        assert not models[1].is_loaded()
        assert models[2].is_loaded()
    
    @pytest.mark.asyncio
    async def test_cleanup_expired(self, mock_config):
        """Test cleanup of expired entries."""
        # Create cache with longer cleanup interval to prevent automatic cleanup
        cache = ModelCache(max_capacity=3, cleanup_interval=300)  # 5 minutes
        await cache.start()
        
        try:
            # Add models with different TTLs
            short_ttl_config = ModelConfig(
                model_type=ModelType.WHISPER_TRANSCRIBER,
                model_name="short_ttl",
                device="cpu",
                ttl_seconds=1
            )
            
            long_ttl_config = ModelConfig(
                model_type=ModelType.WHISPER_TRANSCRIBER,
                model_name="long_ttl",
                device="cpu",
                ttl_seconds=300
            )
            
            # Add short TTL model
            short_model = MockModel("short_model", short_ttl_config)
            await short_model.load()
            await cache.put("short_id", short_model, short_ttl_config)
            
            # Add long TTL model
            long_model = MockModel("long_model", long_ttl_config)
            await long_model.load()
            await cache.put("long_id", long_model, long_ttl_config)
            
            assert cache.size() == 2
            
            # Wait for short TTL to expire
            await asyncio.sleep(1.1)
            
            # Cleanup expired
            cleaned_count = await cache.cleanup_expired()
            
            assert cleaned_count == 1
            assert cache.size() == 1
            assert not cache.contains("short_id")
            assert cache.contains("long_id")
            assert not short_model.is_loaded()
            assert long_model.is_loaded()
        
        finally:
            await cache.stop()
    
    @pytest.mark.asyncio
    async def test_update_existing_model(self, cache, mock_config):
        """Test updating existing model in cache."""
        # Add initial model
        model1 = MockModel("model1", mock_config)
        await model1.load()
        await cache.put("test_id", model1, mock_config)
        
        # Update with new model
        model2 = MockModel("model2", mock_config)
        await model2.load()
        await cache.put("test_id", model2, mock_config)
        
        # Should still have only one entry
        assert cache.size() == 1
        
        # Should get the new model
        retrieved = await cache.get("test_id")
        assert retrieved == model2
    
    def test_get_stats(self, cache, mock_config):
        """Test getting cache statistics."""
        stats = cache.get_stats()
        
        assert 'model_count' in stats
        assert 'max_capacity' in stats
        assert 'total_memory_mb' in stats
        assert 'cache_entries' in stats
        
        assert stats['model_count'] == 0
        assert stats['max_capacity'] == 3
        assert stats['total_memory_mb'] == 0
        assert len(stats['cache_entries']) == 0
    
    @pytest.mark.asyncio
    async def test_get_stats_with_models(self, cache, mock_model, mock_config):
        """Test getting cache statistics with models."""
        await mock_model.load()
        await cache.put("test_id", mock_model, mock_config)
        
        stats = cache.get_stats()
        
        assert stats['model_count'] == 1
        assert stats['total_memory_mb'] == 100  # From mock model
        assert len(stats['cache_entries']) == 1
        
        entry_stats = stats['cache_entries'][0]
        assert entry_stats['model_id'] == "test_id"
        assert entry_stats['model_type'] == "whisper_transcriber"
        assert entry_stats['access_count'] == 0
        assert not entry_stats['is_expired']
        assert entry_stats['memory_mb'] == 100
    
    def test_get_model_ids(self, cache):
        """Test getting list of model IDs."""
        ids = cache.get_model_ids()
        assert ids == []
    
    @pytest.mark.asyncio
    async def test_get_model_ids_with_models(self, cache, mock_config):
        """Test getting list of model IDs with models."""
        # Add models
        for i in range(2):
            model = MockModel(f"model_{i}", mock_config)
            await model.load()
            await cache.put(f"test_id_{i}", model, mock_config)
        
        ids = cache.get_model_ids()
        assert set(ids) == {"test_id_0", "test_id_1"}
    
    @pytest.mark.asyncio
    async def test_background_cleanup(self, mock_config):
        """Test background cleanup task."""
        # Create cache with very short cleanup interval
        cache = ModelCache(max_capacity=3, cleanup_interval=0.5)
        await cache.start()
        
        try:
            # Add model with short TTL
            short_ttl_config = ModelConfig(
                model_type=ModelType.WHISPER_TRANSCRIBER,
                model_name="short_ttl",
                device="cpu",
                ttl_seconds=1
            )
            
            model = MockModel("test_model", short_ttl_config)
            await model.load()
            await cache.put("test_id", model, short_ttl_config)
            
            assert cache.size() == 1
            
            # Wait for TTL to expire and cleanup to run
            await asyncio.sleep(1.5)
            
            # Model should be cleaned up automatically
            assert cache.size() == 0
            assert not model.is_loaded()
            
        finally:
            await cache.stop()
    
    @pytest.mark.asyncio
    async def test_thread_safety_simulation(self, cache, mock_config):
        """Test thread safety by simulating concurrent operations."""
        async def add_model(model_id: str):
            model = MockModel(model_id, mock_config)
            await model.load()
            await cache.put(model_id, model, mock_config)
        
        async def get_model(model_id: str):
            return await cache.get(model_id)
        
        async def remove_model(model_id: str):
            return await cache.remove(model_id)
        
        # Run concurrent operations
        tasks = []
        for i in range(5):
            tasks.append(add_model(f"model_{i}"))
        
        for i in range(5):
            tasks.append(get_model(f"model_{i}"))
        
        for i in range(2):
            tasks.append(remove_model(f"model_{i}"))
        
        # Execute all tasks concurrently
        await asyncio.gather(*tasks, return_exceptions=True)
        
        # Cache should be in a consistent state
        assert cache.size() <= 5  # Some models may have been removed
        
        # All remaining models should be valid
        for model_id in cache.get_model_ids():
            model = await cache.get(model_id)
            assert model is not None
            assert model.is_loaded()


if __name__ == "__main__":
    pytest.main([__file__])