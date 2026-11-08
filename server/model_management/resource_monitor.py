"""
Resource monitoring for the model management system.
"""

import asyncio
import logging
import psutil
import threading
import time
from typing import Callable, Optional, Dict, List, Tuple
from datetime import datetime
from enum import Enum

try:
    import pynvml  # ships with the NVIDIA base image; reads the driver directly, no CUDA context
    pynvml.nvmlInit()
    GPU_AVAILABLE = pynvml.nvmlDeviceGetCount() > 0
except Exception:  # package missing, no driver, or no device visible to the container
    GPU_AVAILABLE = False

from .data_models import MemoryUsage, ModelStatus
from .metrics_collector import MetricsCollector


logger = logging.getLogger(__name__)


class ResourcePressureLevel(Enum):
    """Enumeration of resource pressure levels."""
    NORMAL = "normal"
    WARNING = "warning"
    CRITICAL = "critical"
    EMERGENCY = "emergency"


class EvictionPolicy(Enum):
    """Enumeration of model eviction policies."""
    LRU = "lru"  # Least Recently Used
    PRIORITY_LRU = "priority_lru"  # Priority-based with LRU tiebreaker
    MEMORY_USAGE = "memory_usage"  # Highest memory usage first
    TTL_EXPIRED = "ttl_expired"  # TTL expired models first


class ResourceMonitor:
    """Monitors system resources including memory and GPU usage with advanced policies."""
    
    def __init__(self, memory_threshold_percent: float = 85.0, 
                 gpu_threshold_percent: float = 90.0,
                 monitoring_interval: float = 5.0,
                 metrics_collector: Optional[MetricsCollector] = None,
                 # Advanced resource management settings
                 warning_threshold_percent: float = 70.0,
                 critical_threshold_percent: float = 85.0,
                 emergency_threshold_percent: float = 95.0,
                 eviction_policy: EvictionPolicy = EvictionPolicy.PRIORITY_LRU,
                 min_free_memory_mb: int = 512,
                 gpu_fallback_enabled: bool = True):
        """
        Initialize the resource monitor with advanced policies.
        
        Args:
            memory_threshold_percent: Memory usage threshold for pressure detection
            gpu_threshold_percent: GPU memory threshold for pressure detection  
            monitoring_interval: Interval between monitoring checks in seconds
            metrics_collector: Optional metrics collector for recording resource usage
            warning_threshold_percent: Warning level threshold
            critical_threshold_percent: Critical level threshold
            emergency_threshold_percent: Emergency level threshold
            eviction_policy: Policy for model eviction when resources are constrained
            min_free_memory_mb: Minimum free memory to maintain
            gpu_fallback_enabled: Whether to enable GPU to CPU fallback
        """
        self.memory_threshold = memory_threshold_percent
        self.gpu_threshold = gpu_threshold_percent
        self.monitoring_interval = monitoring_interval
        self._monitoring = False
        self._monitor_thread: Optional[threading.Thread] = None
        self._callback: Optional[Callable[[MemoryUsage], None]] = None
        self._pressure_callback: Optional[Callable[[ResourcePressureLevel, MemoryUsage], None]] = None
        self._lock = threading.Lock()
        self._metrics_collector = metrics_collector
        
        # Advanced resource management settings
        self.warning_threshold = warning_threshold_percent
        self.critical_threshold = critical_threshold_percent
        self.emergency_threshold = emergency_threshold_percent
        self.eviction_policy = eviction_policy
        self.min_free_memory_mb = min_free_memory_mb
        self.gpu_fallback_enabled = gpu_fallback_enabled
        
        # Resource pressure tracking
        self._current_pressure_level = ResourcePressureLevel.NORMAL
        self._pressure_history: List[Tuple[datetime, ResourcePressureLevel]] = []
        self._consecutive_pressure_count = 0
        
    def get_memory_usage(self) -> MemoryUsage:
        """
        Get current system memory usage.
        
        Returns:
            MemoryUsage object with current memory statistics
        """
        # Get system memory info
        memory = psutil.virtual_memory()
        total_mb = memory.total // (1024 * 1024)
        used_mb = memory.used // (1024 * 1024)
        available_mb = memory.available // (1024 * 1024)
        
        # Get GPU memory info if available
        gpu_total_mb = 0
        gpu_used_mb = 0
        
        if GPU_AVAILABLE:
            try:
                # Device-wide numbers for GPU 0 (compose reserves one GPU), so the
                # ASR process and the diarizer subprocess are both counted.
                mem = pynvml.nvmlDeviceGetMemoryInfo(pynvml.nvmlDeviceGetHandleByIndex(0))
                gpu_total_mb = mem.total // (1024 * 1024)
                gpu_used_mb = mem.used // (1024 * 1024)
            except Exception as e:
                logger.warning(f"Failed to get GPU memory info: {e}")

        return MemoryUsage(
            total_mb=total_mb,
            used_mb=used_mb,
            available_mb=available_mb,
            gpu_total_mb=gpu_total_mb,
            gpu_used_mb=gpu_used_mb
        )
    
    def is_memory_pressure(self) -> bool:
        """
        Check if system is under memory pressure.
        
        Returns:
            True if memory usage exceeds threshold
        """
        pressure_level = self.get_pressure_level()
        return pressure_level != ResourcePressureLevel.NORMAL
    
    def get_pressure_level(self) -> ResourcePressureLevel:
        """
        Get current resource pressure level.
        
        Returns:
            ResourcePressureLevel indicating current system pressure
        """
        memory_usage = self.get_memory_usage()
        
        # Determine pressure level based on memory usage
        max_usage_percent = max(
            memory_usage.usage_percentage,
            memory_usage.gpu_usage_percentage if memory_usage.gpu_total_mb > 0 else 0
        )
        
        # Check minimum free memory requirement
        if memory_usage.available_mb < self.min_free_memory_mb:
            max_usage_percent = max(max_usage_percent, 90.0)  # Treat as high pressure
        
        if max_usage_percent >= self.emergency_threshold:
            return ResourcePressureLevel.EMERGENCY
        elif max_usage_percent >= self.critical_threshold:
            return ResourcePressureLevel.CRITICAL
        elif max_usage_percent >= self.warning_threshold:
            return ResourcePressureLevel.WARNING
        else:
            return ResourcePressureLevel.NORMAL
    
    def should_trigger_eviction(self) -> bool:
        """
        Determine if model eviction should be triggered.
        
        Returns:
            True if models should be evicted to free resources
        """
        pressure_level = self.get_pressure_level()
        
        # Trigger eviction for critical or emergency pressure
        if pressure_level in [ResourcePressureLevel.CRITICAL, ResourcePressureLevel.EMERGENCY]:
            return True
        
        # Trigger eviction if we've been under warning pressure for too long
        if pressure_level == ResourcePressureLevel.WARNING and self._consecutive_pressure_count >= 3:
            return True
        
        return False
    
    def get_eviction_candidates(self, loaded_models: Dict[str, ModelStatus]) -> List[str]:
        """
        Get list of model IDs that are candidates for eviction based on policy.
        
        Args:
            loaded_models: Dictionary of currently loaded models
            
        Returns:
            List of model IDs ordered by eviction priority (highest priority first)
        """
        if not loaded_models:
            return []
        
        candidates = []
        current_time = datetime.now()
        
        for model_id, status in loaded_models.items():
            if not status.is_loaded:
                continue
            
            # Calculate metrics for eviction decision
            last_access = status.last_access_time or current_time
            idle_time = (current_time - last_access).total_seconds()
            
            candidates.append({
                'model_id': model_id,
                'status': status,
                'idle_time': idle_time,
                'memory_usage': status.memory_usage_mb,
                'priority': getattr(status, 'priority', 1),  # Default priority if not set
                'access_count': status.access_count
            })
        
        # Sort candidates based on eviction policy
        if self.eviction_policy == EvictionPolicy.LRU:
            candidates.sort(key=lambda x: x['idle_time'], reverse=True)
        elif self.eviction_policy == EvictionPolicy.PRIORITY_LRU:
            # Sort by priority (lower first), then by idle time (higher first)
            candidates.sort(key=lambda x: (x['priority'], -x['idle_time']))
        elif self.eviction_policy == EvictionPolicy.MEMORY_USAGE:
            candidates.sort(key=lambda x: x['memory_usage'], reverse=True)
        elif self.eviction_policy == EvictionPolicy.TTL_EXPIRED:
            # This would need TTL information from model configs
            candidates.sort(key=lambda x: x['idle_time'], reverse=True)
        
        return [candidate['model_id'] for candidate in candidates]
    
    def calculate_eviction_count(self, memory_usage: MemoryUsage, 
                               loaded_models: Dict[str, ModelStatus]) -> int:
        """
        Calculate how many models should be evicted based on current pressure.
        
        Args:
            memory_usage: Current memory usage
            loaded_models: Currently loaded models
            
        Returns:
            Number of models to evict
        """
        pressure_level = self.get_pressure_level()
        
        if pressure_level == ResourcePressureLevel.EMERGENCY:
            # Evict aggressively - up to 75% of loaded models
            return max(1, len(loaded_models) * 3 // 4)
        elif pressure_level == ResourcePressureLevel.CRITICAL:
            # Evict moderately - up to 50% of loaded models
            return max(1, len(loaded_models) // 2)
        elif pressure_level == ResourcePressureLevel.WARNING:
            # Evict conservatively - 1-2 models
            return min(2, len(loaded_models))
        
        return 0
    
    def should_fallback_to_cpu(self, device: str) -> bool:
        """
        Determine if a model should fallback from GPU to CPU.
        
        Args:
            device: Current device assignment
            
        Returns:
            True if should fallback to CPU
        """
        if not self.gpu_fallback_enabled:
            return False
        
        if not device.startswith('cuda') and device != 'gpu':
            return False  # Already on CPU or other device
        
        memory_usage = self.get_memory_usage()
        
        # Fallback if GPU memory pressure is high
        if memory_usage.gpu_total_mb > 0:
            gpu_pressure = memory_usage.gpu_usage_percentage
            if gpu_pressure >= self.critical_threshold:
                return True
        
        return False
    
    def start_monitoring(self, callback: Callable[[MemoryUsage], None],
                        pressure_callback: Optional[Callable[[ResourcePressureLevel, MemoryUsage], None]] = None) -> None:
        """
        Start continuous resource monitoring.
        
        Args:
            callback: Function to call with memory usage updates
            pressure_callback: Optional function to call when pressure level changes
        """
        with self._lock:
            if self._monitoring:
                logger.warning("Resource monitoring is already running")
                return
                
            self._callback = callback
            self._pressure_callback = pressure_callback
            self._monitoring = True
            self._monitor_thread = threading.Thread(
                target=self._monitor_loop,
                daemon=True,
                name="ResourceMonitor"
            )
            self._monitor_thread.start()
            logger.info("Started resource monitoring with advanced policies")
    
    def stop_monitoring(self) -> None:
        """Stop continuous resource monitoring."""
        with self._lock:
            if not self._monitoring:
                return
                
            self._monitoring = False
            if self._monitor_thread:
                self._monitor_thread.join(timeout=self.monitoring_interval + 1.0)
                self._monitor_thread = None
            self._callback = None
            logger.info("Stopped resource monitoring")
    
    def _monitor_loop(self) -> None:
        """Main monitoring loop running in background thread."""
        logger.info(f"Resource monitoring started with {self.monitoring_interval}s interval")
        
        while self._monitoring:
            try:
                memory_usage = self.get_memory_usage()
                current_pressure = self.get_pressure_level()
                
                # Track pressure level changes
                self._update_pressure_tracking(current_pressure)
                
                # Log memory pressure warnings with detailed information
                if current_pressure != ResourcePressureLevel.NORMAL:
                    logger.warning(
                        f"Resource pressure: {current_pressure.value} - "
                        f"System: {memory_usage.usage_percentage:.1f}%, "
                        f"GPU: {memory_usage.gpu_usage_percentage:.1f}%, "
                        f"Available: {memory_usage.available_mb}MB, "
                        f"Consecutive pressure cycles: {self._consecutive_pressure_count}"
                    )
                
                # Record system memory usage in metrics
                if self._metrics_collector:
                    self._metrics_collector.record_system_memory_usage(memory_usage)
                    self._metrics_collector.record_resource_pressure(current_pressure.value)
                
                # Call pressure callback if pressure level changed
                if (self._pressure_callback and 
                    current_pressure != self._current_pressure_level):
                    try:
                        self._pressure_callback(current_pressure, memory_usage)
                    except Exception as e:
                        logger.error(f"Error in pressure callback: {e}")
                
                # Call regular callback if registered
                if self._callback:
                    try:
                        self._callback(memory_usage)
                    except Exception as e:
                        logger.error(f"Error in resource monitor callback: {e}")
                        
            except Exception as e:
                logger.error(f"Error in resource monitoring loop: {e}")
            
            # Sleep for monitoring interval
            time.sleep(self.monitoring_interval)
        
        logger.info("Resource monitoring loop ended")
    
    def _update_pressure_tracking(self, current_pressure: ResourcePressureLevel) -> None:
        """
        Update pressure level tracking and history.
        
        Args:
            current_pressure: Current pressure level
        """
        now = datetime.now()
        
        # Update pressure history
        self._pressure_history.append((now, current_pressure))
        
        # Keep only last 100 entries
        if len(self._pressure_history) > 100:
            self._pressure_history = self._pressure_history[-100:]
        
        # Update consecutive pressure count
        if current_pressure != ResourcePressureLevel.NORMAL:
            if current_pressure == self._current_pressure_level:
                self._consecutive_pressure_count += 1
            else:
                self._consecutive_pressure_count = 1
        else:
            self._consecutive_pressure_count = 0
        
        self._current_pressure_level = current_pressure
    
    def get_detailed_info(self) -> dict:
        """
        Get detailed system resource information for debugging.
        
        Returns:
            Dictionary with detailed resource information
        """
        memory_usage = self.get_memory_usage()
        current_pressure = self.get_pressure_level()
        
        info = {
            "timestamp": datetime.now().isoformat(),
            "memory": {
                "total_mb": memory_usage.total_mb,
                "used_mb": memory_usage.used_mb,
                "available_mb": memory_usage.available_mb,
                "usage_percent": memory_usage.usage_percentage,
                "pressure": memory_usage.usage_percentage > self.memory_threshold
            },
            "cpu": {
                "count": psutil.cpu_count(),
                "usage_percent": psutil.cpu_percent(interval=1)
            },
            "monitoring": {
                "active": self._monitoring,
                "interval": self.monitoring_interval,
                "memory_threshold": self.memory_threshold,
                "gpu_threshold": self.gpu_threshold
            },
            "resource_pressure": {
                "current_level": current_pressure.value,
                "consecutive_cycles": self._consecutive_pressure_count,
                "should_evict": self.should_trigger_eviction(),
                "thresholds": {
                    "warning": self.warning_threshold,
                    "critical": self.critical_threshold,
                    "emergency": self.emergency_threshold
                }
            },
            "policies": {
                "eviction_policy": self.eviction_policy.value,
                "min_free_memory_mb": self.min_free_memory_mb,
                "gpu_fallback_enabled": self.gpu_fallback_enabled
            }
        }
        
        # Add GPU info if available
        if GPU_AVAILABLE:
            info["gpu"] = {
                "available": True,
                "total_mb": memory_usage.gpu_total_mb,
                "used_mb": memory_usage.gpu_used_mb,
                "usage_percent": memory_usage.gpu_usage_percentage,
                "pressure": memory_usage.gpu_usage_percentage > self.gpu_threshold,
                "should_fallback": self.should_fallback_to_cpu("cuda")
            }
        else:
            info["gpu"] = {"available": False}
        
        # Add pressure history summary
        if self._pressure_history:
            recent_history = self._pressure_history[-10:]  # Last 10 entries
            info["pressure_history"] = [
                {"timestamp": ts.isoformat(), "level": level.value}
                for ts, level in recent_history
            ]
            
        return info
    
    def update_thresholds(self, **kwargs) -> None:
        """
        Update resource monitoring thresholds.
        
        Args:
            **kwargs: Threshold values to update
        """
        with self._lock:
            if 'warning_threshold_percent' in kwargs:
                self.warning_threshold = kwargs['warning_threshold_percent']
            if 'critical_threshold_percent' in kwargs:
                self.critical_threshold = kwargs['critical_threshold_percent']
            if 'emergency_threshold_percent' in kwargs:
                self.emergency_threshold = kwargs['emergency_threshold_percent']
            if 'memory_threshold_percent' in kwargs:
                self.memory_threshold = kwargs['memory_threshold_percent']
            if 'gpu_threshold_percent' in kwargs:
                self.gpu_threshold = kwargs['gpu_threshold_percent']
            if 'min_free_memory_mb' in kwargs:
                self.min_free_memory_mb = kwargs['min_free_memory_mb']
            if 'eviction_policy' in kwargs:
                if isinstance(kwargs['eviction_policy'], str):
                    self.eviction_policy = EvictionPolicy(kwargs['eviction_policy'])
                else:
                    self.eviction_policy = kwargs['eviction_policy']
            if 'gpu_fallback_enabled' in kwargs:
                self.gpu_fallback_enabled = kwargs['gpu_fallback_enabled']
        
        logger.info("Updated resource monitoring thresholds")
    
    def get_pressure_statistics(self) -> Dict[str, any]:
        """
        Get statistics about resource pressure over time.
        
        Returns:
            Dictionary with pressure statistics
        """
        if not self._pressure_history:
            return {"total_samples": 0}
        
        # Count pressure levels
        level_counts = {}
        for _, level in self._pressure_history:
            level_counts[level.value] = level_counts.get(level.value, 0) + 1
        
        total_samples = len(self._pressure_history)
        
        return {
            "total_samples": total_samples,
            "level_counts": level_counts,
            "level_percentages": {
                level: (count / total_samples) * 100
                for level, count in level_counts.items()
            },
            "current_level": self._current_pressure_level.value,
            "consecutive_pressure_cycles": self._consecutive_pressure_count,
            "history_span_minutes": (
                (self._pressure_history[-1][0] - self._pressure_history[0][0]).total_seconds() / 60
                if len(self._pressure_history) > 1 else 0
            )
        }