"""
Metrics collection and monitoring for model management system.
"""

import asyncio
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from threading import Lock
from contextlib import nullcontext
from typing import Dict, List, Optional, Any, Deque

from .data_models import ModelType, MemoryUsage

log = logging.getLogger(__name__)


@dataclass
class ModelMetrics:
    """Metrics for a specific model."""
    model_id: str
    model_type: ModelType
    model_name: str
    device: str
    
    # Load/unload metrics
    total_loads: int = 0
    total_unloads: int = 0
    failed_loads: int = 0
    failed_unloads: int = 0
    
    # Timing metrics (in milliseconds)
    load_times: Deque[int] = field(default_factory=lambda: deque(maxlen=100))
    unload_times: Deque[int] = field(default_factory=lambda: deque(maxlen=100))
    
    # Access metrics
    total_accesses: int = 0
    last_access_time: Optional[datetime] = None
    
    # Memory metrics (in MB)
    peak_memory_usage: int = 0
    current_memory_usage: int = 0
    memory_usage_history: Deque[int] = field(default_factory=lambda: deque(maxlen=100))
    
    # Status tracking
    is_currently_loaded: bool = False
    load_start_time: Optional[datetime] = None
    total_loaded_time_seconds: float = 0.0
    
    def __post_init__(self):
        """Initialize deques if they weren't properly initialized."""
        if not isinstance(self.load_times, deque):
            self.load_times = deque(maxlen=100)
        if not isinstance(self.unload_times, deque):
            self.unload_times = deque(maxlen=100)
        if not isinstance(self.memory_usage_history, deque):
            self.memory_usage_history = deque(maxlen=100)
    
    @property
    def average_load_time(self) -> float:
        """Calculate average load time in milliseconds."""
        if not self.load_times:
            return 0.0
        return sum(self.load_times) / len(self.load_times)
    
    @property
    def average_unload_time(self) -> float:
        """Calculate average unload time in milliseconds."""
        if not self.unload_times:
            return 0.0
        return sum(self.unload_times) / len(self.unload_times)
    
    @property
    def load_success_rate(self) -> float:
        """Calculate load success rate as percentage."""
        total_attempts = self.total_loads + self.failed_loads
        if total_attempts == 0:
            return 100.0
        return (self.total_loads / total_attempts) * 100.0
    
    @property
    def unload_success_rate(self) -> float:
        """Calculate unload success rate as percentage."""
        total_attempts = self.total_unloads + self.failed_unloads
        if total_attempts == 0:
            return 100.0
        return (self.total_unloads / total_attempts) * 100.0


@dataclass
class SystemMetrics:
    """System-wide metrics."""
    
    # Overall statistics
    total_models_created: int = 0
    total_models_destroyed: int = 0
    active_models_count: int = 0
    
    # Resource utilization
    memory_usage_history: Deque[MemoryUsage] = field(default_factory=lambda: deque(maxlen=100))
    
    # Performance metrics
    cache_hits: int = 0
    cache_misses: int = 0
    
    # Error tracking
    total_errors: int = 0
    error_types: Dict[str, int] = field(default_factory=dict)
    
    # Cleanup metrics
    cleanup_runs: int = 0
    models_cleaned_up: int = 0
    
    def __post_init__(self):
        """Initialize deques if they weren't properly initialized."""
        if not isinstance(self.memory_usage_history, deque):
            self.memory_usage_history = deque(maxlen=100)
    
    @property
    def cache_hit_rate(self) -> float:
        """Calculate cache hit rate as percentage."""
        total_requests = self.cache_hits + self.cache_misses
        if total_requests == 0:
            return 0.0
        return (self.cache_hits / total_requests) * 100.0
    
    @property
    def average_memory_usage(self) -> float:
        """Calculate average memory usage percentage."""
        if not self.memory_usage_history:
            return 0.0
        return sum(usage.usage_percentage for usage in self.memory_usage_history) / len(self.memory_usage_history)


class MetricsCollector:
    """
    Collects and manages metrics for the model management system.
    
    This class tracks performance statistics, resource utilization,
    and operational metrics for all models and system-wide operations.
    """
    
    def __init__(self):
        """Initialize the metrics collector."""
        self._model_metrics: Dict[str, ModelMetrics] = {}
        self._system_metrics = SystemMetrics()
        # Use nullcontext instead of Lock to prevent blocking in async context
        # Metrics can tolerate minor race conditions
        self._lock = nullcontext()
        self._collection_start_time = datetime.now()
        
        # Monitoring configuration
        self._monitoring_enabled = True
        self._detailed_logging = True
        
        log.info("Metrics collector initialized")
    
    def record_model_load_start(self, model_id: str, model_type: ModelType, 
                               model_name: str, device: str) -> None:
        """
        Record the start of a model load operation.
        
        Args:
            model_id: Unique identifier for the model
            model_type: Type of the model
            model_name: Name of the model
            device: Device the model is being loaded on
        """
        if not self._monitoring_enabled:
            return
        
        with self._lock:
            if model_id not in self._model_metrics:
                self._model_metrics[model_id] = ModelMetrics(
                    model_id=model_id,
                    model_type=model_type,
                    model_name=model_name,
                    device=device
                )
            
            metrics = self._model_metrics[model_id]
            metrics.load_start_time = datetime.now()
            
            if self._detailed_logging:
                log.info(f"Started loading model: {model_id} ({model_name}) on {device}")
    
    def record_model_load_success(self, model_id: str, load_time_ms: int) -> None:
        """
        Record a successful model load operation.
        
        Args:
            model_id: Unique identifier for the model
            load_time_ms: Time taken to load the model in milliseconds
        """
        if not self._monitoring_enabled:
            return
        
        with self._lock:
            if model_id not in self._model_metrics:
                log.warning(f"Recording load success for unknown model: {model_id}")
                return
            
            metrics = self._model_metrics[model_id]
            metrics.total_loads += 1
            metrics.load_times.append(load_time_ms)
            metrics.is_currently_loaded = True
            metrics.load_start_time = datetime.now()
            
            # Update system metrics
            self._system_metrics.total_models_created += 1
            self._system_metrics.active_models_count += 1
            
            if self._detailed_logging:
                log.info(f"Model loaded successfully: {model_id} in {load_time_ms}ms")
    
    def record_model_load_failure(self, model_id: str, error: Exception) -> None:
        """
        Record a failed model load operation.
        
        Args:
            model_id: Unique identifier for the model
            error: Exception that caused the failure
        """
        if not self._monitoring_enabled:
            return
        
        with self._lock:
            if model_id in self._model_metrics:
                self._model_metrics[model_id].failed_loads += 1
            
            # Update system error metrics
            self._system_metrics.total_errors += 1
            error_type = type(error).__name__
            self._system_metrics.error_types[error_type] = (
                self._system_metrics.error_types.get(error_type, 0) + 1
            )
            
            if self._detailed_logging:
                log.error(f"Model load failed: {model_id}: {error}")
    
    def record_model_unload_start(self, model_id: str) -> None:
        """
        Record the start of a model unload operation.
        
        Args:
            model_id: Unique identifier for the model
        """
        if not self._monitoring_enabled:
            return
        
        with self._lock:
            if model_id in self._model_metrics:
                metrics = self._model_metrics[model_id]
                
                # Calculate total loaded time if model was loaded
                if metrics.is_currently_loaded and metrics.load_start_time:
                    loaded_duration = datetime.now() - metrics.load_start_time
                    metrics.total_loaded_time_seconds += loaded_duration.total_seconds()
                
                if self._detailed_logging:
                    log.info(f"Started unloading model: {model_id}")
    
    def record_model_unload_success(self, model_id: str, unload_time_ms: int) -> None:
        """
        Record a successful model unload operation.
        
        Args:
            model_id: Unique identifier for the model
            unload_time_ms: Time taken to unload the model in milliseconds
        """
        if not self._monitoring_enabled:
            return
        
        with self._lock:
            if model_id in self._model_metrics:
                metrics = self._model_metrics[model_id]
                metrics.total_unloads += 1
                metrics.unload_times.append(unload_time_ms)
                metrics.is_currently_loaded = False
                metrics.load_start_time = None
                
                # Update system metrics
                self._system_metrics.active_models_count = max(0, self._system_metrics.active_models_count - 1)
                
                if self._detailed_logging:
                    log.info(f"Model unloaded successfully: {model_id} in {unload_time_ms}ms")
    
    def record_model_unload_failure(self, model_id: str, error: Exception) -> None:
        """
        Record a failed model unload operation.
        
        Args:
            model_id: Unique identifier for the model
            error: Exception that caused the failure
        """
        if not self._monitoring_enabled:
            return
        
        with self._lock:
            if model_id in self._model_metrics:
                self._model_metrics[model_id].failed_unloads += 1
            
            # Update system error metrics
            self._system_metrics.total_errors += 1
            error_type = type(error).__name__
            self._system_metrics.error_types[error_type] = (
                self._system_metrics.error_types.get(error_type, 0) + 1
            )
            
            if self._detailed_logging:
                log.error(f"Model unload failed: {model_id}: {error}")
    
    def record_model_access(self, model_id: str) -> None:
        """
        Record an access to a model.
        
        Args:
            model_id: Unique identifier for the model
        """
        if not self._monitoring_enabled:
            return
        
        with self._lock:
            if model_id in self._model_metrics:
                metrics = self._model_metrics[model_id]
                metrics.total_accesses += 1
                metrics.last_access_time = datetime.now()
    
    def record_memory_usage(self, model_id: str, memory_usage: MemoryUsage) -> None:
        """
        Record memory usage for a specific model.
        
        Args:
            model_id: Unique identifier for the model
            memory_usage: Current memory usage information
        """
        if not self._monitoring_enabled:
            return
        
        with self._lock:
            if model_id in self._model_metrics:
                metrics = self._model_metrics[model_id]
                metrics.current_memory_usage = memory_usage.used_mb
                metrics.memory_usage_history.append(memory_usage.used_mb)
                
                # Update peak memory usage
                if memory_usage.used_mb > metrics.peak_memory_usage:
                    metrics.peak_memory_usage = memory_usage.used_mb
    
    def record_system_memory_usage(self, memory_usage: MemoryUsage) -> None:
        """
        Record system-wide memory usage.
        
        Args:
            memory_usage: Current system memory usage
        """
        if not self._monitoring_enabled:
            return
        
        with self._lock:
            self._system_metrics.memory_usage_history.append(memory_usage)
    
    def record_cache_hit(self, model_id: str) -> None:
        """
        Record a cache hit for model access.
        
        Args:
            model_id: Unique identifier for the model
        """
        if not self._monitoring_enabled:
            return
        
        with self._lock:
            self._system_metrics.cache_hits += 1
            self.record_model_access(model_id)
    
    def record_cache_miss(self, model_id: str) -> None:
        """
        Record a cache miss for model access.
        
        Args:
            model_id: Unique identifier for the model
        """
        if not self._monitoring_enabled:
            return
        
        with self._lock:
            self._system_metrics.cache_misses += 1
    
    def record_cleanup_operation(self, models_cleaned: int) -> None:
        """
        Record a cleanup operation.
        
        Args:
            models_cleaned: Number of models cleaned up
        """
        if not self._monitoring_enabled:
            return
        
        with self._lock:
            self._system_metrics.cleanup_runs += 1
            self._system_metrics.models_cleaned_up += models_cleaned
            
            if self._detailed_logging:
                log.info(f"Cleanup operation completed: {models_cleaned} models cleaned up")
    
    def record_resource_pressure(self, pressure_level: str) -> None:
        """
        Record resource pressure level.
        
        Args:
            pressure_level: Current resource pressure level
        """
        if not self._monitoring_enabled:
            return
        
        with self._lock:
            # Add to system metrics if not already tracked
            if not hasattr(self._system_metrics, 'pressure_events'):
                self._system_metrics.pressure_events = defaultdict(int)
            
            self._system_metrics.pressure_events[pressure_level] += 1
    
    def record_pressure_eviction(self, model_id: str) -> None:
        """
        Record a model eviction due to resource pressure.
        
        Args:
            model_id: ID of the evicted model
        """
        if not self._monitoring_enabled:
            return
        
        with self._lock:
            # Add to system metrics if not already tracked
            if not hasattr(self._system_metrics, 'pressure_evictions'):
                self._system_metrics.pressure_evictions = 0
            
            self._system_metrics.pressure_evictions += 1
            
            if self._detailed_logging:
                log.info(f"Model evicted due to resource pressure: {model_id}")
    
    def record_gpu_fallback(self, model_id: str, original_device: str, fallback_device: str) -> None:
        """
        Record a GPU to CPU fallback event.
        
        Args:
            model_id: ID of the model that fell back
            original_device: Original device assignment
            fallback_device: Fallback device used
        """
        if not self._monitoring_enabled:
            return
        
        with self._lock:
            # Add to system metrics if not already tracked
            if not hasattr(self._system_metrics, 'gpu_fallbacks'):
                self._system_metrics.gpu_fallbacks = 0
            
            self._system_metrics.gpu_fallbacks += 1
            
            if self._detailed_logging:
                log.info(f"GPU fallback: {model_id} from {original_device} to {fallback_device}")
    
    def get_model_metrics(self, model_id: str) -> Optional[ModelMetrics]:
        """
        Get metrics for a specific model.
        
        Args:
            model_id: Unique identifier for the model
        
        Returns:
            ModelMetrics object or None if model not found
        """
        with self._lock:
            return self._model_metrics.get(model_id)
    
    def get_all_model_metrics(self) -> Dict[str, ModelMetrics]:
        """
        Get metrics for all models.
        
        Returns:
            Dictionary mapping model IDs to their metrics
        """
        with self._lock:
            return self._model_metrics.copy()
    
    def get_system_metrics(self) -> SystemMetrics:
        """
        Get system-wide metrics.
        
        Returns:
            SystemMetrics object
        """
        with self._lock:
            return self._system_metrics
    
    def get_performance_summary(self) -> Dict[str, Any]:
        """
        Get a comprehensive performance summary.
        
        Returns:
            Dictionary containing performance statistics
        """
        with self._lock:
            uptime_seconds = (datetime.now() - self._collection_start_time).total_seconds()
            
            # Calculate aggregate model metrics
            total_load_time = 0
            total_loads = 0
            total_memory_usage = 0
            active_models = 0
            
            for metrics in self._model_metrics.values():
                if metrics.load_times:
                    total_load_time += sum(metrics.load_times)
                    total_loads += len(metrics.load_times)
                
                if metrics.is_currently_loaded:
                    active_models += 1
                    total_memory_usage += metrics.current_memory_usage
            
            avg_load_time = total_load_time / total_loads if total_loads > 0 else 0
            
            return {
                "uptime_seconds": uptime_seconds,
                "system_metrics": {
                    "active_models": active_models,
                    "total_models_created": self._system_metrics.total_models_created,
                    "cache_hit_rate": self._system_metrics.cache_hit_rate,
                    "total_errors": self._system_metrics.total_errors,
                    "cleanup_runs": self._system_metrics.cleanup_runs,
                    "models_cleaned_up": self._system_metrics.models_cleaned_up
                },
                "performance_metrics": {
                    "average_load_time_ms": avg_load_time,
                    "total_memory_usage_mb": total_memory_usage,
                    "average_system_memory_usage": self._system_metrics.average_memory_usage
                },
                "error_breakdown": self._system_metrics.error_types.copy()
            }
    
    def reset_metrics(self) -> None:
        """Reset all collected metrics."""
        with self._lock:
            self._model_metrics.clear()
            self._system_metrics = SystemMetrics()
            self._collection_start_time = datetime.now()
            
            log.info("All metrics have been reset")
    
    def enable_monitoring(self) -> None:
        """Enable metrics collection."""
        self._monitoring_enabled = True
        log.info("Metrics collection enabled")
    
    def disable_monitoring(self) -> None:
        """Disable metrics collection."""
        self._monitoring_enabled = False
        log.info("Metrics collection disabled")
    
    def enable_detailed_logging(self) -> None:
        """Enable detailed logging of operations."""
        self._detailed_logging = True
        log.info("Detailed logging enabled")
    
    def disable_detailed_logging(self) -> None:
        """Disable detailed logging of operations."""
        self._detailed_logging = False
        log.info("Detailed logging disabled")
    
    def export_metrics(self) -> Dict[str, Any]:
        """
        Export all metrics in a structured format.
        
        Returns:
            Dictionary containing all metrics data
        """
        with self._lock:
            return {
                "collection_start_time": self._collection_start_time.isoformat(),
                "uptime_seconds": (datetime.now() - self._collection_start_time).total_seconds(),
                "system_metrics": {
                    "total_models_created": self._system_metrics.total_models_created,
                    "total_models_destroyed": self._system_metrics.total_models_destroyed,
                    "active_models_count": self._system_metrics.active_models_count,
                    "cache_hits": self._system_metrics.cache_hits,
                    "cache_misses": self._system_metrics.cache_misses,
                    "cache_hit_rate": self._system_metrics.cache_hit_rate,
                    "total_errors": self._system_metrics.total_errors,
                    "error_types": self._system_metrics.error_types.copy(),
                    "cleanup_runs": self._system_metrics.cleanup_runs,
                    "models_cleaned_up": self._system_metrics.models_cleaned_up,
                    "average_memory_usage": self._system_metrics.average_memory_usage
                },
                "model_metrics": {
                    model_id: {
                        "model_type": metrics.model_type.value,
                        "model_name": metrics.model_name,
                        "device": metrics.device,
                        "total_loads": metrics.total_loads,
                        "total_unloads": metrics.total_unloads,
                        "failed_loads": metrics.failed_loads,
                        "failed_unloads": metrics.failed_unloads,
                        "average_load_time": metrics.average_load_time,
                        "average_unload_time": metrics.average_unload_time,
                        "load_success_rate": metrics.load_success_rate,
                        "unload_success_rate": metrics.unload_success_rate,
                        "total_accesses": metrics.total_accesses,
                        "last_access_time": metrics.last_access_time.isoformat() if metrics.last_access_time else None,
                        "peak_memory_usage": metrics.peak_memory_usage,
                        "current_memory_usage": metrics.current_memory_usage,
                        "is_currently_loaded": metrics.is_currently_loaded,
                        "total_loaded_time_seconds": metrics.total_loaded_time_seconds
                    }
                    for model_id, metrics in self._model_metrics.items()
                }
            }