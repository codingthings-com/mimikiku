"""
Comprehensive async error handling for transcription operations.

This module provides circuit breaker pattern, retry logic with exponential backoff,
and graceful degradation for transcription pipeline operations.
"""

import asyncio
import logging
from typing import Any, Callable, Optional, Dict, List, Union
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

from .circuit_breaker import CircuitBreaker, CircuitBreakerConfig, CircuitBreakerManager
from .retry_handler import RetryHandler, RetryConfig, RetryManager, RETRY_CONFIGS
from .exceptions import (
    ModelManagementError, CircuitBreakerOpenError, RetryExhaustedError,
    ModelTimeoutError, InsufficientResourcesError
)

log = logging.getLogger(__name__)


class TranscriptionErrorType(Enum):
    """Types of transcription errors for categorization."""
    TIMEOUT = "timeout"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    MODEL_ERROR = "model_error"
    NETWORK_ERROR = "network_error"
    VALIDATION_ERROR = "validation_error"
    UNKNOWN = "unknown"


@dataclass
class TranscriptionError:
    """Detailed transcription error information."""
    error_type: TranscriptionErrorType
    message: str
    original_exception: Exception
    timestamp: datetime = field(default_factory=datetime.now)
    connection_id: Optional[str] = None
    chunk_id: Optional[str] = None
    model_id: Optional[str] = None
    retry_count: int = 0
    is_retryable: bool = True


@dataclass
class GracefulDegradationConfig:
    """Configuration for graceful degradation behavior."""
    max_consecutive_failures: int = 3
    failure_window_seconds: int = 60
    degradation_timeout_seconds: int = 300  # 5 minutes
    enable_fallback_models: bool = True
    enable_chunk_skipping: bool = True
    min_success_rate_threshold: float = 0.5  # 50% success rate


class TranscriptionErrorHandler:
    """
    Comprehensive error handler for transcription operations.
    
    Provides circuit breaker protection, retry logic with exponential backoff,
    and graceful degradation when individual chunks fail.
    """
    
    def __init__(self, 
                 circuit_breaker_config: Optional[CircuitBreakerConfig] = None,
                 retry_config: Optional[RetryConfig] = None,
                 degradation_config: Optional[GracefulDegradationConfig] = None):
        """
        Initialize the transcription error handler.
        
        Args:
            circuit_breaker_config: Configuration for circuit breaker
            retry_config: Configuration for retry logic
            degradation_config: Configuration for graceful degradation
        """
        # Initialize circuit breaker manager
        self.circuit_breaker_manager = CircuitBreakerManager()
        self.circuit_breaker_config = circuit_breaker_config or CircuitBreakerConfig(
            failure_threshold=5,
            recovery_timeout=60,
            success_threshold=3,
            timeout=30
        )
        
        # Initialize retry manager
        self.retry_manager = RetryManager()
        self.retry_config = retry_config or RETRY_CONFIGS.get("model_load", RetryConfig())
        
        # Graceful degradation configuration
        self.degradation_config = degradation_config or GracefulDegradationConfig()
        
        # Error tracking for degradation decisions
        self._connection_errors: Dict[str, List[TranscriptionError]] = {}
        self._degraded_connections: Dict[str, datetime] = {}
        
        log.info("TranscriptionErrorHandler initialized")
    
    async def execute_with_protection(self,
                                    operation: Callable,
                                    connection_id: str,
                                    chunk_id: Optional[str] = None,
                                    model_id: Optional[str] = None,
                                    operation_name: str = "transcription",
                                    *args, **kwargs) -> Any:
        """
        Execute a transcription operation with full error protection.
        
        Args:
            operation: The async operation to execute
            connection_id: ID of the connection
            chunk_id: ID of the chunk being processed
            model_id: ID of the model being used
            operation_name: Name of the operation for logging
            *args: Arguments for the operation
            **kwargs: Keyword arguments for the operation
            
        Returns:
            Result of the operation or None if gracefully degraded
            
        Raises:
            Exception: If the error cannot be handled gracefully
        """
        # Check if connection is in degraded state
        # Skip degradation check for model loading operations to allow reconnections
        if "load" not in operation_name.lower() and await self._is_connection_degraded(connection_id):
            log.warning(f"{connection_id} Connection is degraded, skipping {operation_name} - will auto-recover in {self.degradation_config.degradation_timeout_seconds}s")
            return None
        
        # Get circuit breaker for this connection/model combination
        breaker_name = f"{connection_id}_{model_id or 'default'}_{operation_name}"
        circuit_breaker = self.circuit_breaker_manager.get_breaker(
            breaker_name, self.circuit_breaker_config
        )
        
        # Get retry handler
        retry_handler = self.retry_manager.get_handler(
            f"{operation_name}_retry", self.retry_config
        )
        
        try:
            # Execute with circuit breaker and retry protection
            result = await circuit_breaker.call(
                retry_handler.execute_with_retry,
                operation,
                *args,
                operation_name=operation_name,
                model_id=model_id,
                **kwargs
            )
            
            # Record success for degradation tracking
            await self._record_success(connection_id)
            return result
            
        except CircuitBreakerOpenError as e:
            error = TranscriptionError(
                error_type=TranscriptionErrorType.RESOURCE_EXHAUSTED,
                message=f"Circuit breaker open: {e}",
                original_exception=e,
                connection_id=connection_id,
                chunk_id=chunk_id,
                model_id=model_id,
                is_retryable=False
            )
            return await self._handle_error(error)
            
        except RetryExhaustedError as e:
            error = TranscriptionError(
                error_type=self._classify_error(e.last_error),
                message=f"Retry exhausted after {e.attempts} attempts: {e.last_error}",
                original_exception=e,
                connection_id=connection_id,
                chunk_id=chunk_id,
                model_id=model_id,
                retry_count=e.attempts,
                is_retryable=False
            )
            return await self._handle_error(error)
            
        except asyncio.TimeoutError as e:
            error = TranscriptionError(
                error_type=TranscriptionErrorType.TIMEOUT,
                message=f"Operation timeout: {e}",
                original_exception=e,
                connection_id=connection_id,
                chunk_id=chunk_id,
                model_id=model_id,
                is_retryable=True
            )
            return await self._handle_error(error)
            
        except InsufficientResourcesError as e:
            error = TranscriptionError(
                error_type=TranscriptionErrorType.RESOURCE_EXHAUSTED,
                message=f"Insufficient resources: {e}",
                original_exception=e,
                connection_id=connection_id,
                chunk_id=chunk_id,
                model_id=model_id,
                is_retryable=True
            )
            return await self._handle_error(error)
            
        except Exception as e:
            # Check if this is a non-retryable error that should be handled gracefully
            if not self._is_error_retryable(e):
                error = TranscriptionError(
                    error_type=self._classify_error(e),
                    message=f"Non-retryable error: {e}",
                    original_exception=e,
                    connection_id=connection_id,
                    chunk_id=chunk_id,
                    model_id=model_id,
                    is_retryable=False
                )
                return await self._handle_error(error)
            else:
                # For retryable errors, let them bubble up to be caught by retry/circuit breaker
                raise
    
    async def _handle_error(self, error: TranscriptionError) -> Optional[Any]:
        """
        Handle a transcription error with graceful degradation logic.
        
        Args:
            error: The transcription error to handle
            
        Returns:
            None if gracefully degraded, raises exception if not recoverable
        """
        # Record the error for degradation tracking
        await self._record_error(error)
        
        # Log the error appropriately
        if error.error_type in [TranscriptionErrorType.TIMEOUT, TranscriptionErrorType.RESOURCE_EXHAUSTED]:
            log.warning(f"{error.connection_id} {error.message}")
        else:
            log.error(f"{error.connection_id} {error.message}")
        
        # Check if we should degrade this connection
        if await self._should_degrade_connection(error.connection_id):
            await self._degrade_connection(error.connection_id)
            error_count = len(self._connection_errors.get(error.connection_id, []))
            log.warning(
                f"{error.connection_id} Connection degraded due to {error_count} failures "
                f"(threshold: {self.degradation_config.max_consecutive_failures}). "
                f"Last error: {error.error_type.value} - {error.message}"
            )
            return None
        
        # For individual chunk failures, apply graceful degradation
        if self.degradation_config.enable_chunk_skipping:
            # Skip chunks for most error types to maintain pipeline flow
            if error.error_type in [
                TranscriptionErrorType.TIMEOUT,
                TranscriptionErrorType.RESOURCE_EXHAUSTED,
                TranscriptionErrorType.MODEL_ERROR,
                TranscriptionErrorType.VALIDATION_ERROR,  # Also skip validation errors
                TranscriptionErrorType.NETWORK_ERROR
            ]:
                log.info(f"{error.connection_id} Gracefully skipping failed chunk {error.chunk_id}")
                return None
        
        # If we can't handle gracefully, re-raise the original exception
        raise error.original_exception
    
    async def _record_error(self, error: TranscriptionError) -> None:
        """Record an error for degradation tracking."""
        connection_id = error.connection_id
        if not connection_id:
            return
        
        if connection_id not in self._connection_errors:
            self._connection_errors[connection_id] = []
        
        self._connection_errors[connection_id].append(error)
        
        # Clean up old errors outside the failure window
        cutoff_time = datetime.now() - timedelta(seconds=self.degradation_config.failure_window_seconds)
        self._connection_errors[connection_id] = [
            err for err in self._connection_errors[connection_id]
            if err.timestamp > cutoff_time
        ]
    
    async def _record_success(self, connection_id: str) -> None:
        """Record a successful operation for a connection."""
        # Clear recent errors on success to reset degradation tracking
        if connection_id in self._connection_errors:
            # Keep only very recent errors (last 10 seconds) to avoid immediate re-degradation
            recent_cutoff = datetime.now() - timedelta(seconds=10)
            self._connection_errors[connection_id] = [
                err for err in self._connection_errors[connection_id]
                if err.timestamp > recent_cutoff
            ]
    
    async def _should_degrade_connection(self, connection_id: str) -> bool:
        """
        Determine if a connection should be degraded based on error history.
        
        Args:
            connection_id: ID of the connection to check
            
        Returns:
            True if the connection should be degraded
        """
        if not connection_id or connection_id not in self._connection_errors:
            return False
        
        recent_errors = self._connection_errors[connection_id]
        
        # Check consecutive failures
        if len(recent_errors) >= self.degradation_config.max_consecutive_failures:
            # Check if the last N errors were consecutive
            last_n_errors = recent_errors[-self.degradation_config.max_consecutive_failures:]
            time_span = (last_n_errors[-1].timestamp - last_n_errors[0].timestamp).total_seconds()
            
            if time_span <= self.degradation_config.failure_window_seconds:
                return True
        
        return False
    
    async def _degrade_connection(self, connection_id: str) -> None:
        """Mark a connection as degraded."""
        self._degraded_connections[connection_id] = datetime.now()
        log.warning(f"{connection_id} Connection marked as degraded")
    
    async def _is_connection_degraded(self, connection_id: str) -> bool:
        """
        Check if a connection is currently in degraded state.
        
        Args:
            connection_id: ID of the connection to check
            
        Returns:
            True if the connection is degraded
        """
        if connection_id not in self._degraded_connections:
            return False
        
        degraded_time = self._degraded_connections[connection_id]
        degradation_duration = datetime.now() - degraded_time
        
        # Check if degradation timeout has passed
        if degradation_duration.total_seconds() > self.degradation_config.degradation_timeout_seconds:
            # Remove from degraded state
            del self._degraded_connections[connection_id]
            log.info(f"{connection_id} Connection recovered from degraded state")
            return False
        
        return True
    
    def _classify_error(self, exception: Exception) -> TranscriptionErrorType:
        """
        Classify an exception into a transcription error type.
        
        Args:
            exception: The exception to classify
            
        Returns:
            TranscriptionErrorType for the exception
        """
        if isinstance(exception, (asyncio.TimeoutError, ModelTimeoutError)):
            return TranscriptionErrorType.TIMEOUT
        elif isinstance(exception, InsufficientResourcesError):
            return TranscriptionErrorType.RESOURCE_EXHAUSTED
        elif isinstance(exception, (ConnectionError, OSError)):
            return TranscriptionErrorType.NETWORK_ERROR
        elif isinstance(exception, (ValueError, TypeError)):
            return TranscriptionErrorType.VALIDATION_ERROR
        elif isinstance(exception, ModelManagementError):
            return TranscriptionErrorType.MODEL_ERROR
        else:
            return TranscriptionErrorType.UNKNOWN
    
    def _is_error_retryable(self, exception: Exception) -> bool:
        """
        Determine if an error is retryable.
        
        Args:
            exception: The exception to check
            
        Returns:
            True if the error is retryable
        """
        # Timeout and resource errors are generally retryable
        if isinstance(exception, (asyncio.TimeoutError, InsufficientResourcesError)):
            return True
        
        # Network errors are retryable
        if isinstance(exception, (ConnectionError, OSError)):
            return True
        
        # Some model errors are retryable
        if isinstance(exception, ModelManagementError):
            error_msg = str(exception).lower()
            retryable_keywords = [
                "timeout", "busy", "unavailable", "temporary", "connection"
            ]
            return any(keyword in error_msg for keyword in retryable_keywords)
        
        # Validation errors are generally not retryable
        if isinstance(exception, (ValueError, TypeError)):
            return False
        
        # Default to not retryable for unknown errors to prevent infinite loops
        return False
    
    async def recover_connection(self, connection_id: str) -> None:
        """
        Manually recover a connection from degraded state.
        
        Args:
            connection_id: ID of the connection to recover
        """
        if connection_id in self._degraded_connections:
            del self._degraded_connections[connection_id]
            log.info(f"{connection_id} Connection manually recovered from degraded state")
        
        # Clear error history
        if connection_id in self._connection_errors:
            self._connection_errors[connection_id] = []
    
    def get_connection_error_stats(self, connection_id: str) -> Dict[str, Any]:
        """
        Get error statistics for a connection.
        
        Args:
            connection_id: ID of the connection
            
        Returns:
            Dictionary containing error statistics
        """
        stats = {
            "connection_id": connection_id,
            "is_degraded": connection_id in self._degraded_connections,
            "degraded_since": None,
            "recent_errors": 0,
            "error_types": {},
            "success_rate": 1.0
        }
        
        if connection_id in self._degraded_connections:
            stats["degraded_since"] = self._degraded_connections[connection_id].isoformat()
        
        if connection_id in self._connection_errors:
            recent_errors = self._connection_errors[connection_id]
            stats["recent_errors"] = len(recent_errors)
            
            # Count error types
            for error in recent_errors:
                error_type = error.error_type.value
                stats["error_types"][error_type] = stats["error_types"].get(error_type, 0) + 1
        
        return stats
    
    def get_all_error_stats(self) -> Dict[str, Any]:
        """
        Get comprehensive error statistics for all connections.
        
        Returns:
            Dictionary containing all error statistics
        """
        stats = {
            "total_connections_tracked": len(self._connection_errors),
            "degraded_connections": len(self._degraded_connections),
            "connections": {}
        }
        
        # Get stats for all tracked connections
        all_connection_ids = set(self._connection_errors.keys()) | set(self._degraded_connections.keys())
        
        for connection_id in all_connection_ids:
            stats["connections"][connection_id] = self.get_connection_error_stats(connection_id)
        
        return stats
    
    async def cleanup_connection(self, connection_id: str) -> None:
        """
        Clean up error tracking for a connection.
        
        Args:
            connection_id: ID of the connection to clean up
        """
        if connection_id in self._connection_errors:
            del self._connection_errors[connection_id]
        
        if connection_id in self._degraded_connections:
            del self._degraded_connections[connection_id]
        
        log.debug(f"{connection_id} Error tracking cleaned up")


# Predefined configurations for different transcription scenarios
TRANSCRIPTION_ERROR_CONFIGS = {
    "whisper_transcription": {
        "circuit_breaker": CircuitBreakerConfig(
            failure_threshold=10,  # Much more relaxed
            recovery_timeout=60,
            success_threshold=2,
            timeout=30  # Longer timeout
        ),
        "retry": RetryConfig(
            max_attempts=2,
            base_delay=1.0,
            max_delay=10.0,
            exponential_base=2.0,
            jitter=True
        ),
        "degradation": GracefulDegradationConfig(
            max_consecutive_failures=50,  # Very relaxed - basically disabled for local network
            failure_window_seconds=60,
            degradation_timeout_seconds=30,  # Shorter recovery time
            enable_chunk_skipping=True
        )
    },
    "diarization": {
        "circuit_breaker": CircuitBreakerConfig(
            failure_threshold=5,
            recovery_timeout=60,
            success_threshold=3,
            timeout=30
        ),
        "retry": RetryConfig(
            max_attempts=3,
            base_delay=2.0,
            max_delay=20.0,
            exponential_base=2.0,
            jitter=True
        ),
        "degradation": GracefulDegradationConfig(
            max_consecutive_failures=3,
            failure_window_seconds=60,
            degradation_timeout_seconds=300,
            enable_chunk_skipping=True
        )
    }
}


def create_transcription_error_handler(config_name: str = "whisper_transcription") -> TranscriptionErrorHandler:
    """
    Create a transcription error handler with predefined configuration.
    
    Args:
        config_name: Name of the configuration to use
        
    Returns:
        Configured TranscriptionErrorHandler instance
    """
    config = TRANSCRIPTION_ERROR_CONFIGS.get(config_name, TRANSCRIPTION_ERROR_CONFIGS["whisper_transcription"])
    
    return TranscriptionErrorHandler(
        circuit_breaker_config=config["circuit_breaker"],
        retry_config=config["retry"],
        degradation_config=config["degradation"]
    )