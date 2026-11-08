"""
Retry handler with exponential backoff for model operations.
"""

import asyncio
import logging
import random
from typing import Callable, Any, Optional, List, Type
from dataclasses import dataclass
from datetime import datetime, timedelta

from .exceptions import RetryExhaustedError, ModelManagementError

log = logging.getLogger(__name__)


@dataclass
class RetryConfig:
    """Configuration for retry behavior."""
    max_attempts: int = 3
    base_delay: float = 1.0  # Base delay in seconds
    max_delay: float = 60.0  # Maximum delay in seconds
    exponential_base: float = 2.0  # Exponential backoff base
    jitter: bool = True  # Add random jitter to delays
    retryable_exceptions: List[Type[Exception]] = None
    
    def __post_init__(self):
        if self.retryable_exceptions is None:
            # Default retryable exceptions
            self.retryable_exceptions = [
                ConnectionError,
                TimeoutError,
                OSError,
                # Add model-specific transient errors
            ]


class RetryHandler:
    """
    Handles retry logic with exponential backoff and jitter.
    
    This class provides robust retry mechanisms for transient failures
    in model operations, with configurable backoff strategies.
    """
    
    def __init__(self, config: RetryConfig):
        """
        Initialize the retry handler.
        
        Args:
            config: Retry configuration
        """
        self.config = config
    
    async def execute_with_retry(
        self, 
        func: Callable, 
        *args, 
        operation_name: str = "operation",
        model_id: Optional[str] = None,
        model_type: Optional[str] = None,
        **kwargs
    ) -> Any:
        """
        Execute a function with retry logic.
        
        Args:
            func: Function to execute
            *args: Positional arguments for the function
            operation_name: Name of the operation for logging
            model_id: ID of the model being operated on
            model_type: Type of the model being operated on
            **kwargs: Keyword arguments for the function
        
        Returns:
            Result of the function call
        
        Raises:
            RetryExhaustedError: If all retry attempts are exhausted
            Exception: If the exception is not retryable
        """
        last_error = None
        
        for attempt in range(1, self.config.max_attempts + 1):
            try:
                log.debug(f"Attempting {operation_name} (attempt {attempt}/{self.config.max_attempts})")
                
                # Execute the function
                if asyncio.iscoroutinefunction(func):
                    result = await func(*args, **kwargs)
                else:
                    result = await asyncio.to_thread(func, *args, **kwargs)
                
                if attempt > 1:
                    log.info(f"{operation_name} succeeded on attempt {attempt}")
                
                return result
                
            except Exception as e:
                last_error = e
                
                # Check if this exception is retryable
                if not self._is_retryable_exception(e):
                    log.error(f"{operation_name} failed with non-retryable error: {e}")
                    raise
                
                # If this is the last attempt, don't wait
                if attempt == self.config.max_attempts:
                    break
                
                # Calculate delay for next attempt
                delay = self._calculate_delay(attempt)
                
                log.warning(
                    f"{operation_name} failed on attempt {attempt}/{self.config.max_attempts}: {e}. "
                    f"Retrying in {delay:.2f} seconds..."
                )
                
                await asyncio.sleep(delay)
        
        # All attempts exhausted
        error_msg = (
            f"{operation_name} failed after {self.config.max_attempts} attempts. "
            f"Last error: {last_error}"
        )
        
        raise RetryExhaustedError(
            error_msg,
            attempts=self.config.max_attempts,
            last_error=last_error,
            model_id=model_id,
            model_type=model_type
        )
    
    def _is_retryable_exception(self, exception: Exception) -> bool:
        """
        Check if an exception is retryable.
        
        Args:
            exception: Exception to check
        
        Returns:
            True if the exception is retryable, False otherwise
        """
        # Check if it's in the configured retryable exceptions
        for exc_type in self.config.retryable_exceptions:
            if isinstance(exception, exc_type):
                return True
        
        # Check for specific model management errors that are retryable
        if isinstance(exception, ModelManagementError):
            # Some model management errors are retryable (e.g., temporary resource issues)
            error_msg = str(exception).lower()
            retryable_keywords = [
                "timeout", "connection", "temporary", "busy", "unavailable",
                "resource temporarily unavailable", "device busy"
            ]
            return any(keyword in error_msg for keyword in retryable_keywords)
        
        return False
    
    def _calculate_delay(self, attempt: int) -> float:
        """
        Calculate the delay for the next retry attempt.
        
        Args:
            attempt: Current attempt number (1-based)
        
        Returns:
            Delay in seconds
        """
        # Calculate exponential backoff
        delay = self.config.base_delay * (self.config.exponential_base ** (attempt - 1))
        
        # Apply maximum delay limit
        delay = min(delay, self.config.max_delay)
        
        # Add jitter if enabled
        if self.config.jitter:
            # Add random jitter of ±25%
            jitter_range = delay * 0.25
            jitter = random.uniform(-jitter_range, jitter_range)
            delay += jitter
        
        # Ensure delay is not negative
        return max(0.1, delay)
    
    def update_config(self, config: RetryConfig) -> None:
        """Update the retry configuration."""
        self.config = config
        log.info("Retry handler configuration updated")


class RetryManager:
    """
    Manager for retry handlers with different configurations.
    
    This class manages retry handlers for different types of operations,
    allowing for customized retry behavior based on operation type.
    """
    
    def __init__(self):
        """Initialize the retry manager."""
        self._handlers: dict[str, RetryHandler] = {}
        self._default_config = RetryConfig()
    
    def get_handler(self, name: str, config: Optional[RetryConfig] = None) -> RetryHandler:
        """
        Get or create a retry handler.
        
        Args:
            name: Name of the retry handler
            config: Configuration for the retry handler (uses default if None)
        
        Returns:
            RetryHandler instance
        """
        if name not in self._handlers:
            handler_config = config or self._default_config
            self._handlers[name] = RetryHandler(handler_config)
            log.info(f"Created retry handler '{name}'")
        
        return self._handlers[name]
    
    def set_default_config(self, config: RetryConfig) -> None:
        """Set the default configuration for new retry handlers."""
        self._default_config = config
        log.info("Updated default retry configuration")
    
    def get_handler_configs(self) -> dict[str, RetryConfig]:
        """Get configurations for all retry handlers."""
        return {name: handler.config for name, handler in self._handlers.items()}


# Predefined retry configurations for common scenarios
RETRY_CONFIGS = {
    "model_load": RetryConfig(
        max_attempts=3,
        base_delay=2.0,
        max_delay=30.0,
        exponential_base=2.0,
        jitter=True
    ),
    "model_unload": RetryConfig(
        max_attempts=2,
        base_delay=1.0,
        max_delay=10.0,
        exponential_base=2.0,
        jitter=False
    ),
    "resource_check": RetryConfig(
        max_attempts=5,
        base_delay=0.5,
        max_delay=5.0,
        exponential_base=1.5,
        jitter=True
    ),
    "fallback_switch": RetryConfig(
        max_attempts=2,
        base_delay=1.0,
        max_delay=5.0,
        exponential_base=2.0,
        jitter=False
    )
}