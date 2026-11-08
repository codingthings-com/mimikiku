"""
Model cache with TTL and LRU eviction policies.
"""

import asyncio
import logging
import threading
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Tuple
from dataclasses import dataclass

from .base_model import BaseModel
from .data_models import ModelConfig
from .constants import (
    DEFAULT_CACHE_MAX_CAPACITY,
    DEFAULT_CACHE_CLEANUP_INTERVAL
)

log = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    """Entry in the model cache."""
    model: BaseModel
    config: ModelConfig
    created_at: datetime
    last_accessed: datetime
    access_count: int = 0
    
    def is_expired(self) -> bool:
        """Check if this cache entry has expired based on TTL."""
        if self.config.ttl_seconds <= 0:
            return False  # No expiration
        
        age = datetime.now() - self.created_at
        return age.total_seconds() > self.config.ttl_seconds
    
    def update_access(self) -> None:
        """Update access time and count."""
        self.last_accessed = datetime.now()
        self.access_count += 1
        self.model.update_access_time()


class ModelCache:
    """
    Thread-safe model cache with TTL and LRU eviction policies.
    
    This cache manages loaded models with the following features:
    - Time-to-live (TTL) based expiration
    - Least-recently-used (LRU) eviction when capacity is reached
    - Thread-safe operations for concurrent access
    - Automatic cleanup of expired entries
    - Memory usage tracking and limits
    """
    
    def __init__(self, max_capacity: int = DEFAULT_CACHE_MAX_CAPACITY, cleanup_interval: int = DEFAULT_CACHE_CLEANUP_INTERVAL):
        """
        Initialize the model cache.
        
        Args:
            max_capacity: Maximum number of models to keep in cache
            cleanup_interval: Interval in seconds for automatic cleanup
        """
        self._max_capacity = max_capacity
        self._cleanup_interval = cleanup_interval
        
        # Thread-safe cache storage using OrderedDict for LRU
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.RLock()  # Reentrant lock for nested calls
        
        # Background cleanup task
        self._cleanup_task: Optional[asyncio.Task] = None
        self._running = False
    
    async def start(self) -> None:
        """Start the cache and background cleanup task."""
        if self._running:
            return
        
        self._running = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        log.info(f"Model cache started with capacity {self._max_capacity}")
    
    async def stop(self) -> None:
        """Stop the cache and cleanup all models."""
        self._running = False
        
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        
        # Clear all cached models
        await self.clear()
        log.info("Model cache stopped")
    
    async def get(self, model_id: str) -> Optional[BaseModel]:
        """
        Get a model from the cache.
        
        Args:
            model_id: ID of the model to retrieve
        
        Returns:
            BaseModel instance if found and valid, None otherwise
        """
        with self._lock:
            entry = self._cache.get(model_id)
            
            if entry is None:
                log.debug(f"Cache miss for model: {model_id}")
                return None
            
            # Check if entry has expired
            if entry.is_expired():
                log.debug(f"Cache entry expired for model: {model_id}")
                # Remove expired entry synchronously
                del self._cache[model_id]
                # Schedule async cleanup
                asyncio.create_task(self._unload_model_async(entry.model))
                return None
            
            # Check if model is still loaded
            if not entry.model.is_loaded():
                log.debug(f"Cached model no longer loaded: {model_id}")
                # Remove invalid entry synchronously
                del self._cache[model_id]
                return None
            
            # Update access information and move to end (most recent)
            entry.update_access()
            self._cache.move_to_end(model_id)
            
            log.debug(f"Cache hit for model: {model_id}")
            return entry.model
    
    async def put(self, model_id: str, model: BaseModel, config: ModelConfig) -> None:
        """
        Put a model into the cache.
        
        Args:
            model_id: ID of the model
            model: The model instance to cache
            config: Configuration for the model
        """
        with self._lock:
            now = datetime.now()
            
            # If model already exists, update it
            if model_id in self._cache:
                entry = self._cache[model_id]
                entry.model = model
                entry.config = config
                entry.created_at = now
                entry.last_accessed = now
                entry.access_count = 0
                self._cache.move_to_end(model_id)
                log.debug(f"Updated cached model: {model_id}")
                return
            
            # Check capacity and evict if necessary
            await self._ensure_capacity()
            
            # Create new cache entry
            entry = CacheEntry(
                model=model,
                config=config,
                created_at=now,
                last_accessed=now,
                access_count=0
            )
            
            self._cache[model_id] = entry
            log.debug(f"Cached new model: {model_id}")
    
    async def remove(self, model_id: str) -> bool:
        """
        Remove a model from the cache.
        
        Args:
            model_id: ID of the model to remove
        
        Returns:
            True if model was removed, False if not found
        """
        return await self._remove_entry(model_id)
    
    async def clear(self) -> None:
        """Clear all models from the cache."""
        with self._lock:
            model_ids = list(self._cache.keys())
        
        # Unload all models
        for model_id in model_ids:
            await self._remove_entry(model_id)
        
        log.info("Cache cleared")
    
    def get_stats(self) -> Dict:
        """
        Get cache statistics.
        
        Returns:
            Dictionary containing cache statistics
        """
        with self._lock:
            total_memory_mb = 0
            model_count = len(self._cache)
            
            for entry in self._cache.values():
                if entry.model.is_loaded():
                    memory_usage = entry.model.get_memory_usage()
                    total_memory_mb += memory_usage.used_mb
            
            return {
                'model_count': model_count,
                'max_capacity': self._max_capacity,
                'total_memory_mb': total_memory_mb,
                'cache_entries': [
                    {
                        'model_id': model_id,
                        'model_type': entry.config.model_type.value,
                        'created_at': entry.created_at.isoformat(),
                        'last_accessed': entry.last_accessed.isoformat(),
                        'access_count': entry.access_count,
                        'is_expired': entry.is_expired(),
                        'memory_mb': entry.model.get_memory_usage().used_mb if entry.model.is_loaded() else 0
                    }
                    for model_id, entry in self._cache.items()
                ]
            }
    
    def get_model_ids(self) -> List[str]:
        """Get list of all cached model IDs."""
        with self._lock:
            return list(self._cache.keys())
    
    def contains(self, model_id: str) -> bool:
        """Check if a model is in the cache."""
        with self._lock:
            return model_id in self._cache
    
    def size(self) -> int:
        """Get the current number of cached models."""
        with self._lock:
            return len(self._cache)
    
    async def cleanup_expired(self) -> int:
        """
        Clean up expired cache entries.
        
        Returns:
            Number of entries cleaned up
        """
        with self._lock:
            expired_ids = [
                model_id for model_id, entry in self._cache.items()
                if entry.is_expired()
            ]
        
        cleanup_count = 0
        for model_id in expired_ids:
            if await self._remove_entry(model_id):
                cleanup_count += 1
        
        if cleanup_count > 0:
            log.info(f"Cleaned up {cleanup_count} expired cache entries")
        
        return cleanup_count
    
    async def evict_lru(self, count: int = 1) -> int:
        """
        Evict least recently used models.
        
        Args:
            count: Number of models to evict
        
        Returns:
            Number of models actually evicted
        """
        evicted_count = 0
        
        for _ in range(count):
            with self._lock:
                if not self._cache:
                    break
                
                # Get the least recently used model (first in OrderedDict)
                lru_model_id = next(iter(self._cache))
            
            if await self._remove_entry(lru_model_id):
                evicted_count += 1
                log.debug(f"Evicted LRU model: {lru_model_id}")
        
        if evicted_count > 0:
            log.info(f"Evicted {evicted_count} LRU models")
        
        return evicted_count
    
    async def _ensure_capacity(self) -> None:
        """Ensure cache has capacity for one more entry."""
        # Check capacity without holding lock during eviction
        needs_eviction = False
        with self._lock:
            if len(self._cache) >= self._max_capacity:
                needs_eviction = True
        
        if needs_eviction:
            # Evict one LRU model to make space (don't hold lock during async operation)
            await self.evict_lru(1)
    
    async def _remove_entry(self, model_id: str) -> bool:
        """
        Remove a cache entry and unload the model.
        
        Args:
            model_id: ID of the model to remove
        
        Returns:
            True if entry was removed, False if not found
        """
        with self._lock:
            entry = self._cache.pop(model_id, None)
        
        if entry is None:
            return False
        
        # Unload the model
        try:
            await entry.model.unload()
            log.debug(f"Unloaded model from cache: {model_id}")
        except Exception as e:
            log.error(f"Error unloading cached model {model_id}: {e}")
        
        return True
    
    async def _cleanup_loop(self) -> None:
        """Background loop for cleaning up expired entries."""
        while self._running:
            try:
                await self.cleanup_expired()
            except Exception as e:
                log.error(f"Error in cache cleanup loop: {e}")
            
            # Wait for next cleanup cycle
            try:
                await asyncio.sleep(self._cleanup_interval)
            except asyncio.CancelledError:
                break
        
        log.debug("Cache cleanup loop stopped")
    
    async def _unload_model_async(self, model: BaseModel) -> None:
        """Asynchronously unload a model."""
        try:
            await model.unload()
        except Exception as e:
            log.error(f"Error unloading model: {e}")