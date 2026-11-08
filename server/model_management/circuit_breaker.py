"""
Circuit breaker pattern implementation for model management.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from enum import Enum
from typing import Callable, Any, Optional, Dict
from dataclasses import dataclass

from .exceptions import CircuitBreakerOpenError

log = logging.getLogger(__name__)


class CircuitBreakerState(Enum):
    """States of the circuit breaker."""
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Blocking all requests
    HALF_OPEN = "half_open"  # Testing if service is recovered


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker."""
    failure_threshold: int = 5  # Number of failures before opening
    recovery_timeout: int = 60  # Seconds to wait before trying half-open
    success_threshold: int = 3  # Successes needed in half-open to close
    timeout: int = 30  # Timeout for operations in seconds


class CircuitBreaker:
    """
    Circuit breaker implementation for protecting against cascading failures.
    
    The circuit breaker monitors the failure rate of operations and can
    temporarily block requests when the failure rate is too high, allowing
    the system to recover.
    """
    
    def __init__(self, name: str, config: CircuitBreakerConfig):
        """
        Initialize the circuit breaker.
        
        Args:
            name: Name of the circuit breaker for logging
            config: Configuration for the circuit breaker
        """
        self.name = name
        self.config = config
        self.state = CircuitBreakerState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: Optional[datetime] = None
        self.last_success_time: Optional[datetime] = None
        self._lock = asyncio.Lock()
    
    async def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute a function through the circuit breaker.
        
        Args:
            func: Function to execute
            *args: Positional arguments for the function
            **kwargs: Keyword arguments for the function
        
        Returns:
            Result of the function call
        
        Raises:
            CircuitBreakerOpenError: If circuit breaker is open
            Exception: Any exception raised by the function
        """
        async with self._lock:
            await self._update_state()
            
            if self.state == CircuitBreakerState.OPEN:
                raise CircuitBreakerOpenError(
                    f"Circuit breaker '{self.name}' is open. "
                    f"Last failure: {self.last_failure_time}"
                )
        
        try:
            # Execute with timeout
            result = await asyncio.wait_for(
                func(*args, **kwargs) if asyncio.iscoroutinefunction(func) 
                else asyncio.to_thread(func, *args, **kwargs),
                timeout=self.config.timeout
            )
            
            await self._on_success()
            return result
            
        except Exception as e:
            await self._on_failure(e)
            raise
    
    async def _update_state(self) -> None:
        """Update the circuit breaker state based on current conditions."""
        now = datetime.now()
        
        if self.state == CircuitBreakerState.OPEN:
            # Check if we should transition to half-open
            if (self.last_failure_time and 
                now - self.last_failure_time >= timedelta(seconds=self.config.recovery_timeout)):
                self.state = CircuitBreakerState.HALF_OPEN
                self.success_count = 0
                log.info(f"Circuit breaker '{self.name}' transitioned to HALF_OPEN")
        
        elif self.state == CircuitBreakerState.HALF_OPEN:
            # Check if we should close (enough successes) or open (any failure)
            if self.success_count >= self.config.success_threshold:
                self.state = CircuitBreakerState.CLOSED
                self.failure_count = 0
                self.success_count = 0
                log.info(f"Circuit breaker '{self.name}' transitioned to CLOSED")
    
    async def _on_success(self) -> None:
        """Handle successful operation."""
        async with self._lock:
            self.last_success_time = datetime.now()
            
            if self.state == CircuitBreakerState.HALF_OPEN:
                self.success_count += 1
                # Check if we should transition to closed
                if self.success_count >= self.config.success_threshold:
                    self.state = CircuitBreakerState.CLOSED
                    self.failure_count = 0
                    self.success_count = 0
                    log.info(f"Circuit breaker '{self.name}' transitioned to CLOSED")
            elif self.state == CircuitBreakerState.CLOSED:
                # Reset failure count on success
                self.failure_count = 0
    
    async def _on_failure(self, error: Exception) -> None:
        """Handle failed operation."""
        async with self._lock:
            self.last_failure_time = datetime.now()
            self.failure_count += 1
            
            log.warning(f"Circuit breaker '{self.name}' recorded failure: {error}")
            
            if self.state == CircuitBreakerState.CLOSED:
                if self.failure_count >= self.config.failure_threshold:
                    self.state = CircuitBreakerState.OPEN
                    log.error(f"Circuit breaker '{self.name}' opened after {self.failure_count} failures")
            
            elif self.state == CircuitBreakerState.HALF_OPEN:
                # Any failure in half-open state opens the circuit
                self.state = CircuitBreakerState.OPEN
                self.success_count = 0
                log.error(f"Circuit breaker '{self.name}' reopened due to failure in HALF_OPEN state")
    
    def get_state(self) -> CircuitBreakerState:
        """Get the current state of the circuit breaker."""
        return self.state
    
    def get_stats(self) -> Dict[str, Any]:
        """Get statistics about the circuit breaker."""
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "last_failure_time": self.last_failure_time,
            "last_success_time": self.last_success_time,
            "config": {
                "failure_threshold": self.config.failure_threshold,
                "recovery_timeout": self.config.recovery_timeout,
                "success_threshold": self.config.success_threshold,
                "timeout": self.config.timeout
            }
        }
    
    async def reset(self) -> None:
        """Reset the circuit breaker to closed state."""
        async with self._lock:
            self.state = CircuitBreakerState.CLOSED
            self.failure_count = 0
            self.success_count = 0
            self.last_failure_time = None
            self.last_success_time = None
            log.info(f"Circuit breaker '{self.name}' has been reset")


class CircuitBreakerManager:
    """
    Manager for multiple circuit breakers.
    
    This class manages circuit breakers for different model types and operations,
    providing a centralized way to configure and monitor circuit breakers.
    """
    
    def __init__(self):
        """Initialize the circuit breaker manager."""
        self._breakers: Dict[str, CircuitBreaker] = {}
        self._default_config = CircuitBreakerConfig()
    
    def get_breaker(self, name: str, config: Optional[CircuitBreakerConfig] = None) -> CircuitBreaker:
        """
        Get or create a circuit breaker.
        
        Args:
            name: Name of the circuit breaker
            config: Configuration for the circuit breaker (uses default if None)
        
        Returns:
            CircuitBreaker instance
        """
        if name not in self._breakers:
            breaker_config = config or self._default_config
            self._breakers[name] = CircuitBreaker(name, breaker_config)
            log.info(f"Created circuit breaker '{name}'")
        
        return self._breakers[name]
    
    def get_all_stats(self) -> Dict[str, Dict[str, Any]]:
        """Get statistics for all circuit breakers."""
        return {name: breaker.get_stats() for name, breaker in self._breakers.items()}
    
    async def reset_all(self) -> None:
        """Reset all circuit breakers."""
        for breaker in self._breakers.values():
            await breaker.reset()
        log.info("All circuit breakers have been reset")
    
    async def reset_breaker(self, name: str) -> None:
        """Reset a specific circuit breaker."""
        if name in self._breakers:
            await self._breakers[name].reset()
        else:
            log.warning(f"Circuit breaker '{name}' not found")
    
    def set_default_config(self, config: CircuitBreakerConfig) -> None:
        """Set the default configuration for new circuit breakers."""
        self._default_config = config
        log.info("Updated default circuit breaker configuration")