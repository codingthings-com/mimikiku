"""
Abstract base class for all managed models.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from .data_models import MemoryUsage


class BaseModel(ABC):
    """
    Abstract base class that defines the interface for all managed models.
    
    All models in the system must implement this interface to support
    dynamic loading/unloading and resource management.
    """
    
    def __init__(self, model_id: str, config: Any):
        """
        Initialize the base model.
        
        Args:
            model_id: Unique identifier for this model instance
            config: Model-specific configuration object
        """
        self.model_id = model_id
        self.config = config
        self._is_loaded = False
        self._last_access_time = None
        self._load_time_ms = 0
        self._access_count = 0
    
    @abstractmethod
    async def load(self) -> None:
        """
        Load the model into memory.
        
        This method should handle all initialization required to make
        the model ready for inference.
        
        Raises:
            ModelLoadError: If the model fails to load
            InsufficientResourcesError: If system lacks required resources
        """
        pass
    
    @abstractmethod
    async def unload(self) -> None:
        """
        Unload the model from memory and free resources.
        
        This method should clean up all resources used by the model,
        including GPU memory, CPU memory, and any other system resources.
        """
        pass
    
    @abstractmethod
    def is_loaded(self) -> bool:
        """
        Check if the model is currently loaded in memory.
        
        Returns:
            True if the model is loaded and ready for inference, False otherwise
        """
        pass
    
    @abstractmethod
    def get_memory_usage(self) -> MemoryUsage:
        """
        Get current memory usage of the model.
        
        Returns:
            MemoryUsage object containing memory statistics
        """
        pass
    
    def get_last_access_time(self) -> datetime:
        """
        Get the timestamp of the last access to this model.
        
        Returns:
            Datetime of last access, or None if never accessed
        """
        return self._last_access_time
    
    def update_access_time(self) -> None:
        """Update the last access time to current time."""
        self._last_access_time = datetime.now()
        self._access_count += 1
    
    def get_access_count(self) -> int:
        """Get the total number of times this model has been accessed."""
        return self._access_count
    
    def get_load_time_ms(self) -> int:
        """Get the time it took to load this model in milliseconds."""
        return self._load_time_ms
    
    def set_load_time_ms(self, load_time_ms: int) -> None:
        """Set the load time for this model."""
        self._load_time_ms = load_time_ms