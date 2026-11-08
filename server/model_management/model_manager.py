"""
Central model manager for orchestrating model operations.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Any
import uuid

from .base_model import BaseModel
from .model_registry import ModelRegistry
from .data_models import ModelType, ModelConfig, ModelStatus
from .exceptions import (
    ModelNotFoundError, ModelLoadError, ModelUnloadError, 
    CircuitBreakerOpenError, RetryExhaustedError, FallbackExhaustedError,
    ConfigurationError
)
from .circuit_breaker import CircuitBreakerManager, CircuitBreakerConfig
from .retry_handler import RetryManager, RETRY_CONFIGS
from .fallback_manager import FallbackManager
from .metrics_collector import MetricsCollector
from .resource_monitor import ResourceMonitor, ResourcePressureLevel
from .websocket_session_tracker import WebSocketSessionTracker
from .constants import (
    DEFAULT_CLEANUP_INTERVAL,
    DEFAULT_CIRCUIT_BREAKER_FAILURE_THRESHOLD,
    DEFAULT_CIRCUIT_BREAKER_RECOVERY_TIMEOUT,
    DEFAULT_CIRCUIT_BREAKER_SUCCESS_THRESHOLD,
    DEFAULT_CIRCUIT_BREAKER_TIMEOUT
)

log = logging.getLogger(__name__)


class ModelManager:
    """
    Central orchestrator for all model lifecycle operations.
    
    This class manages the loading, unloading, and lifecycle of all models
    in the system, coordinating with the registry and cache systems.
    Includes comprehensive error handling with circuit breakers, retry logic,
    and fallback mechanisms.
    """
    
    def __init__(self, registry: ModelRegistry, 
                 metrics_collector: Optional[MetricsCollector] = None,
                 resource_monitor: Optional[ResourceMonitor] = None,
                 websocket_session_tracker: Optional[WebSocketSessionTracker] = None):
        """
        Initialize the model manager.
        
        Args:
            registry: Model registry for creating model instances
            metrics_collector: Optional metrics collector for monitoring
            resource_monitor: Optional resource monitor for advanced resource management
            websocket_session_tracker: Optional WebSocket session tracker for session protection
        """
        self._registry = registry
        self._loaded_models: Dict[str, BaseModel] = {}
        self._model_configs: Dict[str, ModelConfig] = {}
        self._model_loading_locks: Dict[str, asyncio.Lock] = {}  # Per-model locks
        self._cleanup_task: Optional[asyncio.Task] = None
        self._cleanup_interval = DEFAULT_CLEANUP_INTERVAL
        self._running = False
        
        # Metrics collection
        self._metrics_collector = metrics_collector or MetricsCollector()
        
        # Resource monitoring with advanced policies
        self._resource_monitor = resource_monitor or ResourceMonitor(
            metrics_collector=self._metrics_collector
        )
        
        # WebSocket session tracking for model protection
        self._websocket_session_tracker = websocket_session_tracker or WebSocketSessionTracker()
        
        # Error handling components
        self._circuit_breaker_manager = CircuitBreakerManager()
        self._retry_manager = RetryManager()
        self._fallback_manager = FallbackManager()
        
        # Initialize retry handlers with predefined configurations
        for name, config in RETRY_CONFIGS.items():
            self._retry_manager.get_handler(name, config)
        
        # Configure circuit breakers for different operations
        self._setup_circuit_breakers()
    
    async def start(self) -> None:
        """Start the model manager and background cleanup task."""
        if self._running:
            return
        
        self._running = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        
        # Start WebSocket session tracker
        await self._websocket_session_tracker.start()
        
        # Start resource monitoring with pressure callback
        self._resource_monitor.start_monitoring(
            callback=self._on_memory_usage_update,
            pressure_callback=self._on_pressure_level_change
        )
        
        log.info("Model manager started with advanced resource management and WebSocket session protection")
    
    async def stop(self) -> None:
        """Stop the model manager and cleanup all models."""
        self._running = False
        
        # Stop WebSocket session tracker
        await self._websocket_session_tracker.stop()
        
        # Stop resource monitoring
        self._resource_monitor.stop_monitoring()
        
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        
        # Unload all models (ignoring WebSocket protection during shutdown)
        for model_id in list(self._loaded_models.keys()):
            await self._safe_unload_model(model_id, force=True)
        
        log.info("Model manager stopped")
    
    async def get_model(self, model_type: ModelType, config: ModelConfig) -> BaseModel:
        """
        Get a model instance, loading it if necessary.
        
        Uses comprehensive error handling including circuit breakers,
        retry logic, and fallback mechanisms. Uses per-model locks to prevent
        concurrent loading of the same model.
        
        Args:
            model_type: The type of model to get
            config: Configuration for the model
        
        Returns:
            BaseModel instance ready for use
        
        Raises:
            ModelNotFoundError: If the model type is not registered
            ModelLoadError: If the model fails to load
            CircuitBreakerOpenError: If circuit breaker is open
            FallbackExhaustedError: If all fallback attempts fail
        """
        # Generate a unique model ID based on type and config
        model_id = self._generate_model_id(model_type, config)
        
        # Quick check without lock - if model is loaded, return it immediately
        log.debug(f"get_model called for {model_id}, checking if already loaded")
        if model_id in self._loaded_models:
            model = self._loaded_models[model_id]
            
            # Type check to prevent the 'dict' object has no attribute 'is_loaded' error
            if not hasattr(model, 'is_loaded'):
                log.error(f"Invalid model object in _loaded_models for {model_id}: {type(model)}. Removing corrupted entry.")
                del self._loaded_models[model_id]
                if model_id in self._model_configs:
                    del self._model_configs[model_id]
            else:
                is_loaded = model.is_loaded()
                log.debug(f"Model {model_id} found in loaded_models, is_loaded={is_loaded}")
                if is_loaded:
                    log.debug(f"Model {model_id} is loaded, updating access time")
                    model.update_access_time()
                    log.debug(f"Model {model_id} recording cache hit")
                    self._metrics_collector.record_cache_hit(model_id)
                    log.info(f"Returning existing loaded model: {model_id}")
                    return model
                else:
                    log.warning(f"Model {model_id} found but not loaded, will reload")
        else:
            log.debug(f"Model {model_id} not in loaded_models")
        
        # Get or create a lock for this specific model
        if model_id not in self._model_loading_locks:
            self._model_loading_locks[model_id] = asyncio.Lock()
            log.debug(f"Created new lock for model {model_id}")
        
        model_lock = self._model_loading_locks[model_id]
        log.debug(f"Attempting to acquire lock for model {model_id}")
        
        # Acquire lock to prevent concurrent loading of the same model
        async with model_lock:
            log.debug(f"Lock acquired for model {model_id}")
            
            # Double-check if model was loaded while waiting for lock
            if model_id in self._loaded_models:
                model = self._loaded_models[model_id]
                if model.is_loaded():
                    model.update_access_time()
                    self._metrics_collector.record_cache_hit(model_id)
                    log.info(f"Model {model_id} was loaded by another connection, returning existing")
                    return model
                else:
                    # Model exists but not loaded, remove it
                    log.warning(f"Model {model_id} exists but not loaded, removing")
                    await self._safe_unload_model(model_id)
            
            # Record cache miss for new model loading
            self._metrics_collector.record_cache_miss(model_id)
            log.info(f"Loading new model: {model_id}")
            
            # Try to load the model with comprehensive error handling
            return await self._load_model_with_fallbacks(model_type, config, model_id)
    
    async def release_model(self, model_id: str) -> None:
        """
        Release a model, unloading it from memory.
        
        Args:
            model_id: ID of the model to release
        """
        await self._safe_unload_model(model_id)
    
    async def cleanup_idle_models(self) -> None:
        """Clean up models that have exceeded their TTL, respecting WebSocket session protection."""
        current_time = datetime.now()
        models_to_cleanup = []
        protected_models = self._websocket_session_tracker.get_protected_models()
        
        for model_id, model in self._loaded_models.items():
            config = self._model_configs.get(model_id)
            if not config:
                continue
            
            # Type check to prevent the 'dict' object has no attribute error
            if not hasattr(model, 'get_last_access_time'):
                log.error(f"Invalid model object in _loaded_models for {model_id}: {type(model)}. Skipping cleanup.")
                continue
            
            last_access = model.get_last_access_time()
            if last_access is None:
                continue
            
            idle_time = current_time - last_access
            if idle_time.total_seconds() > config.ttl_seconds:
                # Check if model can be unloaded (WebSocket session protection)
                if self._websocket_session_tracker.can_unload_model(model_id, is_pressure_situation=False):
                    models_to_cleanup.append(model_id)
                else:
                    log.debug(f"Skipping cleanup of model {model_id} - protected by active WebSocket session")
        
        cleanup_count = 0
        for model_id in models_to_cleanup:
            log.info(f"Cleaning up idle model: {model_id}")
            if await self._safe_unload_model(model_id):
                cleanup_count += 1
        
        # Record cleanup operation
        if cleanup_count > 0:
            self._metrics_collector.record_cleanup_operation(cleanup_count)
            
        # Log protection status
        if protected_models:
            log.debug(f"Models protected from cleanup by WebSocket sessions: {protected_models}")
    
    async def get_model_status(self) -> Dict[str, ModelStatus]:
        """
        Get status information for all loaded models.
        
        Returns:
            Dictionary mapping model IDs to their status
        """
        status_dict = {}
        current_time = datetime.now()
        protected_models = self._websocket_session_tracker.get_protected_models()
        
        for model_id, model in self._loaded_models.items():
            config = self._model_configs.get(model_id)
            if not config:
                continue
            
            # Type check to prevent attribute errors
            if not hasattr(model, 'get_memory_usage'):
                log.error(f"Invalid model object in _loaded_models for {model_id}: {type(model)}. Skipping status.")
                continue
            
            memory_usage = model.get_memory_usage()
            last_access = model.get_last_access_time()
            
            # Calculate idle time
            idle_seconds = None
            if last_access:
                idle_seconds = (current_time - last_access).total_seconds()
            
            status = ModelStatus(
                model_id=model_id,
                model_type=config.model_type,
                is_loaded=model.is_loaded(),
                memory_usage_mb=memory_usage.used_mb,
                last_access_time=last_access,
                load_time_ms=model.get_load_time_ms(),
                access_count=model.get_access_count()
            )
            
            # Add additional runtime info (not part of dataclass)
            status_dict[model_id] = {
                **status.__dict__,
                "idle_seconds": idle_seconds,
                "ttl_seconds": config.ttl_seconds,
                "is_protected": model_id in protected_models,
                "can_cleanup": idle_seconds is not None and idle_seconds > config.ttl_seconds and model_id not in protected_models
            }
        
        return status_dict
    
    async def update_configuration(self, config_updates: Dict) -> None:
        """
        Update configuration for the model manager.
        
        Args:
            config_updates: Dictionary of configuration updates
        """
        # Update cleanup interval if provided
        if 'cleanup_interval' in config_updates:
            self._cleanup_interval = config_updates['cleanup_interval']
            log.info(f"Updated cleanup interval to {self._cleanup_interval} seconds")
        
        # Additional configuration updates can be added here
        log.info("Model manager configuration updated")
    
    def _generate_model_id(self, model_type: ModelType, config: ModelConfig) -> str:
        """
        Generate a unique model ID based on type and configuration.
        
        Args:
            model_type: The model type
            config: The model configuration
        
        Returns:
            Unique model ID string
        """
        # Create a deterministic ID based on model type, name, and device
        base_id = f"{model_type.value}_{config.model_name}_{config.device}"
        # Add a hash of other config parameters to ensure uniqueness
        config_hash = hash((config.ttl_seconds, config.max_memory_mb, config.priority))
        model_id = f"{base_id}_{abs(config_hash)}"
        log.debug(f"Generated model_id: {model_id}")
        return model_id
    

    
    def _setup_circuit_breakers(self) -> None:
        """Setup circuit breakers for different model operations."""
        # Circuit breaker for model loading operations
        load_config = CircuitBreakerConfig(
            failure_threshold=DEFAULT_CIRCUIT_BREAKER_FAILURE_THRESHOLD,
            recovery_timeout=DEFAULT_CIRCUIT_BREAKER_RECOVERY_TIMEOUT,
            success_threshold=DEFAULT_CIRCUIT_BREAKER_SUCCESS_THRESHOLD,
            timeout=DEFAULT_CIRCUIT_BREAKER_TIMEOUT
        )
        
        # Circuit breaker for model unloading operations
        unload_config = CircuitBreakerConfig(
            failure_threshold=5,  # Allow more failures for unload operations
            recovery_timeout=60,  # 1 minute
            success_threshold=1,
            timeout=30  # 30 seconds timeout for unload operations
        )
        
        self._circuit_breaker_manager.get_breaker("model_load", load_config)
        self._circuit_breaker_manager.get_breaker("model_unload", unload_config)
    
    async def _load_model_with_fallbacks(
        self, 
        model_type: ModelType, 
        config: ModelConfig, 
        model_id: str
    ) -> BaseModel:
        """
        Load a model with fallback support and error handling.
        
        Args:
            model_type: The type of model to load
            config: Primary model configuration
            model_id: ID for the model instance
        
        Returns:
            Successfully loaded model instance
        
        Raises:
            FallbackExhaustedError: If all fallback attempts fail
            CircuitBreakerOpenError: If circuit breaker is open
        """
        circuit_breaker = self._circuit_breaker_manager.get_breaker("model_load")
        retry_handler = self._retry_manager.get_handler("model_load")
        
        try:
            # Use circuit breaker and retry logic with fallback chain
            return await circuit_breaker.call(
                self._try_fallback_chain_with_retry,
                model_type, config, model_id, retry_handler
            )
        except CircuitBreakerOpenError:
            log.error(f"Circuit breaker open for model loading: {model_id}")
            raise
        except Exception as e:
            log.error(f"Failed to load model with fallbacks: {model_id}: {e}")
            raise
    
    async def _try_fallback_chain_with_retry(
        self, 
        model_type: ModelType, 
        config: ModelConfig, 
        model_id: str,
        retry_handler
    ) -> BaseModel:
        """
        Try to load a model using fallback chain with retry logic.
        
        Args:
            model_type: The type of model to load
            config: Primary model configuration
            model_id: ID for the model instance
            retry_handler: Retry handler for transient failures
        
        Returns:
            Successfully loaded model instance
        """
        return await self._fallback_manager.try_fallback_chain(
            config,
            self._create_and_load_model_with_retry,
            model_id
        )
    

    
    async def _safe_unload_model(self, model_id: str, force: bool = False, is_pressure_situation: bool = False) -> bool:
        """
        Safely unload a model with error handling and WebSocket session protection.
        
        Args:
            model_id: ID of the model to unload
            force: If True, bypass WebSocket session protection
            is_pressure_situation: If True, this is due to memory pressure
            
        Returns:
            True if model was unloaded, False if protected or failed
        """
        if model_id not in self._loaded_models:
            return False
        
        # Check WebSocket session protection unless forced
        if not force and not self._websocket_session_tracker.can_unload_model(model_id, is_pressure_situation):
            log.debug(f"Model {model_id} unload blocked by WebSocket session protection")
            return False
        
        circuit_breaker = self._circuit_breaker_manager.get_breaker("model_unload")
        retry_handler = self._retry_manager.get_handler("model_unload")
        
        try:
            await circuit_breaker.call(
                retry_handler.execute_with_retry,
                self._unload_model_operation,
                model_id,
                operation_name=f"unload_model_{model_id}",
                model_id=model_id
            )
            return True
        except Exception as e:
            log.error(f"Failed to unload model {model_id}: {e}")
            # Force removal from tracking even if unload fails
            if model_id in self._loaded_models:
                del self._loaded_models[model_id]
            if model_id in self._model_configs:
                del self._model_configs[model_id]
            return False
    
    async def _unload_model_operation(self, model_id: str) -> None:
        """
        Core unload operation for a model.
        
        Args:
            model_id: ID of the model to unload
        """
        model = self._loaded_models[model_id]
        
        # Record unload start
        self._metrics_collector.record_model_unload_start(model_id)
        
        try:
            # Record unload start time
            unload_start = datetime.now()
            
            await model.unload()
            
            # Record unload time
            unload_time_ms = int((datetime.now() - unload_start).total_seconds() * 1000)
            
            # Record successful unload
            self._metrics_collector.record_model_unload_success(model_id, unload_time_ms)
            
            log.info(f"Unloaded model: {model_id}")
        
        except Exception as e:
            # Record failed unload
            self._metrics_collector.record_model_unload_failure(model_id, e)
            raise
        
        finally:
            # Remove from tracking
            if model_id in self._loaded_models:
                del self._loaded_models[model_id]
            if model_id in self._model_configs:
                del self._model_configs[model_id]
    
    def register_fallback_chain(self, model_type: ModelType, model_name: str, 
                              device: str, fallback_configs: List[ModelConfig]) -> None:
        """
        Register a fallback chain for a specific model configuration.
        
        Args:
            model_type: Type of the primary model
            model_name: Name of the primary model
            device: Device of the primary model
            fallback_configs: List of fallback configurations
        """
        primary_key = f"{model_type.value}_{model_name}_{device}"
        self._fallback_manager.register_fallback_chain(primary_key, fallback_configs)
    
    def get_error_statistics(self) -> Dict[str, Any]:
        """
        Get comprehensive error handling statistics.
        
        Returns:
            Dictionary containing error handling statistics
        """
        return {
            "circuit_breakers": self._circuit_breaker_manager.get_all_stats(),
            "retry_handlers": self._retry_manager.get_handler_configs(),
            "fallback_manager": self._fallback_manager.get_statistics(),
            "failed_attempts": self._fallback_manager.get_failed_attempts()
        }
    
    async def reset_error_handling(self) -> None:
        """Reset all error handling components."""
        await self._circuit_breaker_manager.reset_all()
        self._fallback_manager.clear_failed_attempts()
        log.info("Reset all error handling components")
    
    def get_metrics_collector(self) -> MetricsCollector:
        """Get the metrics collector instance."""
        return self._metrics_collector
    
    def get_performance_metrics(self) -> Dict[str, Any]:
        """Get comprehensive performance metrics."""
        return self._metrics_collector.get_performance_summary()

    def get_resource_snapshot(self) -> Dict[str, Any]:
        """Measured host/GPU memory and the current pressure level."""
        usage = self._resource_monitor.get_memory_usage()
        return {
            "ram_total_mb": usage.total_mb,
            "ram_used_mb": usage.used_mb,
            "ram_percent": usage.usage_percentage,
            "gpu_total_mb": usage.gpu_total_mb,
            "gpu_used_mb": usage.gpu_used_mb,
            "gpu_percent": usage.gpu_usage_percentage,
            "pressure": self._resource_monitor.get_pressure_level().value,
        }
    
    async def _cleanup_loop(self) -> None:
        """Background loop for cleaning up idle models."""
        while self._running:
            try:
                await self.cleanup_idle_models()
                
                # Check if resource pressure requires additional eviction
                if self._resource_monitor.should_trigger_eviction():
                    await self._handle_resource_pressure_eviction()
                    
            except Exception as e:
                log.error(f"Error in cleanup loop: {e}")
            
            # Wait for next cleanup cycle
            try:
                await asyncio.sleep(self._cleanup_interval)
            except asyncio.CancelledError:
                break
        
        log.debug("Cleanup loop stopped")
    
    def _on_memory_usage_update(self, memory_usage) -> None:
        """
        Callback for memory usage updates.
        
        Args:
            memory_usage: Current memory usage information
        """
        # This can be used for additional monitoring or logging
        pass
    
    async def _on_pressure_level_change(self, pressure_level: ResourcePressureLevel, memory_usage) -> None:
        """
        Callback for resource pressure level changes.
        
        Args:
            pressure_level: New pressure level
            memory_usage: Current memory usage
        """
        log.info(f"Resource pressure level changed to: {pressure_level.value}")
        
        # Trigger immediate eviction for critical/emergency pressure
        if pressure_level in [ResourcePressureLevel.CRITICAL, ResourcePressureLevel.EMERGENCY]:
            try:
                await self._handle_resource_pressure_eviction()
            except Exception as e:
                log.error(f"Error handling pressure level change: {e}")
    
    async def _handle_resource_pressure_eviction(self) -> None:
        """Handle model eviction due to resource pressure, respecting WebSocket session protection."""
        memory_usage = self._resource_monitor.get_memory_usage()
        model_statuses = await self.get_model_status()
        
        # Get eviction candidates
        candidates = self._resource_monitor.get_eviction_candidates(model_statuses)
        if not candidates:
            log.debug("No eviction candidates available")
            return
        
        # Filter candidates based on WebSocket session protection
        protected_models = self._websocket_session_tracker.get_protected_models()
        unprotected_candidates = [
            model_id for model_id in candidates 
            if self._websocket_session_tracker.can_unload_model(model_id, is_pressure_situation=True)
        ]
        
        if not unprotected_candidates:
            log.warning(f"All eviction candidates are protected by WebSocket sessions: {candidates}")
            log.warning("Consider increasing memory limits or reducing model TTL if pressure persists")
            return
        
        # Calculate how many models to evict
        eviction_count = self._resource_monitor.calculate_eviction_count(memory_usage, model_statuses)
        if eviction_count <= 0:
            return
        
        # Evict models (only unprotected ones)
        models_to_evict = unprotected_candidates[:eviction_count]
        protected_count = len([m for m in candidates[:eviction_count] if m in protected_models])
        
        log.info(f"Evicting {len(models_to_evict)} models due to resource pressure: {models_to_evict}")
        if protected_count > 0:
            log.info(f"Skipped {protected_count} models protected by WebSocket sessions")
        
        evicted_count = 0
        for model_id in models_to_evict:
            try:
                if await self._safe_unload_model(model_id, is_pressure_situation=True):
                    self._metrics_collector.record_pressure_eviction(model_id)
                    evicted_count += 1
            except Exception as e:
                log.error(f"Failed to evict model {model_id}: {e}")
        
        log.info(f"Successfully evicted {evicted_count}/{len(models_to_evict)} models under pressure")
    
    async def _create_and_load_model_with_retry(
        self, 
        model_type: ModelType, 
        model_id: str, 
        config: ModelConfig
    ) -> BaseModel:
        """
        Create and load a model with retry logic and GPU fallback.
        
        Args:
            model_type: The type of model to create
            model_id: ID for the model instance
            config: Model configuration
        
        Returns:
            Successfully loaded model instance
        """
        retry_handler = self._retry_manager.get_handler("model_load")
        
        # Check if we should fallback to CPU due to GPU pressure
        original_device = config.device
        if self._resource_monitor.should_fallback_to_cpu(config.device):
            log.info(f"Falling back to CPU for model {model_id} due to GPU pressure")
            config = ModelConfig(
                model_type=config.model_type,
                model_name=config.model_name,
                device="cpu",
                ttl_seconds=config.ttl_seconds,
                max_memory_mb=config.max_memory_mb,
                fallback_configs=config.fallback_configs,
                lazy_loading=config.lazy_loading,
                priority=config.priority
            )
            self._metrics_collector.record_gpu_fallback(model_id, original_device, "cpu")
        
        async def _load_operation():
            # Record load start
            self._metrics_collector.record_model_load_start(
                model_id, model_type, config.model_name, config.device
            )
            
            try:
                # Create the model
                model = self._registry.create_model(model_type, model_id, config)
                
                # Record load start time
                load_start = datetime.now()
                
                # Load the model
                await model.load()
                
                # Record load time
                load_time_ms = int((datetime.now() - load_start).total_seconds() * 1000)
                model.set_load_time_ms(load_time_ms)
                
                # Store model and config (with validation)
                if not hasattr(model, 'is_loaded') or not hasattr(model, 'get_last_access_time'):
                    raise ConfigurationError(f"Invalid model object created for {model_id}: {type(model)}")
                
                self._loaded_models[model_id] = model
                self._model_configs[model_id] = config
                
                # Update access time
                model.update_access_time()
                
                # Record successful load
                self._metrics_collector.record_model_load_success(model_id, load_time_ms)
                
                log.info(f"Loaded model: {model_id} ({config.model_name}) on {config.device} in {load_time_ms}ms")
                return model
            
            except Exception as e:
                # Record failed load
                self._metrics_collector.record_model_load_failure(model_id, e)
                raise
        
        return await retry_handler.execute_with_retry(
            _load_operation,
            operation_name=f"load_model_{config.model_name}",
            model_id=model_id,
            model_type=model_type.value
        )
    
    def get_resource_monitor(self) -> ResourceMonitor:
        """Get the resource monitor instance."""
        return self._resource_monitor
    
    def get_websocket_session_tracker(self) -> WebSocketSessionTracker:
        """Get the WebSocket session tracker instance."""
        return self._websocket_session_tracker
    
    def register_websocket_session(self, connection_id: str, model_ids: set):
        """
        Register a WebSocket session with associated models.
        
        Args:
            connection_id: ID of the WebSocket connection
            model_ids: Set of model IDs used by this session
        """
        self._websocket_session_tracker.register_session(connection_id, model_ids)
    
    def update_websocket_session_activity(self, connection_id: str):
        """
        Update activity for a WebSocket session.
        
        Args:
            connection_id: ID of the WebSocket connection
        """
        self._websocket_session_tracker.update_session_activity(connection_id)
    
    def disconnect_websocket_session(self, connection_id: str):
        """
        Mark a WebSocket session as disconnected.
        
        Args:
            connection_id: ID of the WebSocket connection
        """
        self._websocket_session_tracker.disconnect_session(connection_id)
    
    async def force_eviction(self, count: int = 1) -> List[str]:
        """
        Force eviction of models for testing or manual resource management.
        
        Args:
            count: Number of models to evict
            
        Returns:
            List of evicted model IDs
        """
        model_statuses = await self.get_model_status()
        candidates = self._resource_monitor.get_eviction_candidates(model_statuses)
        
        models_to_evict = candidates[:count]
        evicted_models = []
        
        for model_id in models_to_evict:
            try:
                if await self._safe_unload_model(model_id, force=True):  # Force eviction bypasses protection
                    evicted_models.append(model_id)
                    log.info(f"Force evicted model: {model_id}")
            except Exception as e:
                log.error(f"Failed to force evict model {model_id}: {e}")
        
        return evicted_models