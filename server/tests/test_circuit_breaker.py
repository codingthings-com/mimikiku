"""
Unit tests for circuit breaker implementation.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, Mock
from datetime import datetime, timedelta

from server.model_management.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerState,
    CircuitBreakerConfig,
    CircuitBreakerManager
)
from server.model_management.exceptions import CircuitBreakerOpenError


class TestCircuitBreakerConfig:
    """Test CircuitBreakerConfig dataclass."""
    
    def test_default_values(self):
        """Test default configuration values."""
        config = CircuitBreakerConfig()
        assert config.failure_threshold == 5
        assert config.recovery_timeout == 60
        assert config.success_threshold == 3
        assert config.timeout == 30
    
    def test_custom_values(self):
        """Test custom configuration values."""
        config = CircuitBreakerConfig(
            failure_threshold=3,
            recovery_timeout=120,
            success_threshold=2,
            timeout=45
        )
        assert config.failure_threshold == 3
        assert config.recovery_timeout == 120
        assert config.success_threshold == 2
        assert config.timeout == 45


class TestCircuitBreaker:
    """Test CircuitBreaker class."""
    
    @pytest.fixture
    def config(self):
        """Create a test configuration."""
        return CircuitBreakerConfig(
            failure_threshold=2,
            recovery_timeout=1,  # 1 second for fast testing
            success_threshold=2,
            timeout=5
        )
    
    @pytest.fixture
    def circuit_breaker(self, config):
        """Create a circuit breaker for testing."""
        return CircuitBreaker("test_breaker", config)
    
    def test_initial_state(self, circuit_breaker):
        """Test initial circuit breaker state."""
        assert circuit_breaker.state == CircuitBreakerState.CLOSED
        assert circuit_breaker.failure_count == 0
        assert circuit_breaker.success_count == 0
        assert circuit_breaker.last_failure_time is None
        assert circuit_breaker.last_success_time is None
    
    @pytest.mark.asyncio
    async def test_successful_call(self, circuit_breaker):
        """Test successful function call through circuit breaker."""
        async def success_func():
            return "success"
        
        result = await circuit_breaker.call(success_func)
        assert result == "success"
        assert circuit_breaker.state == CircuitBreakerState.CLOSED
        assert circuit_breaker.failure_count == 0
    
    @pytest.mark.asyncio
    async def test_failed_call(self, circuit_breaker):
        """Test failed function call through circuit breaker."""
        async def fail_func():
            raise ValueError("Test error")
        
        with pytest.raises(ValueError):
            await circuit_breaker.call(fail_func)
        
        assert circuit_breaker.failure_count == 1
        assert circuit_breaker.last_failure_time is not None
    
    @pytest.mark.asyncio
    async def test_circuit_opens_after_failures(self, circuit_breaker):
        """Test that circuit opens after threshold failures."""
        async def fail_func():
            raise ValueError("Test error")
        
        # First failure
        with pytest.raises(ValueError):
            await circuit_breaker.call(fail_func)
        assert circuit_breaker.state == CircuitBreakerState.CLOSED
        
        # Second failure - should open circuit
        with pytest.raises(ValueError):
            await circuit_breaker.call(fail_func)
        assert circuit_breaker.state == CircuitBreakerState.OPEN
    
    @pytest.mark.asyncio
    async def test_circuit_blocks_when_open(self, circuit_breaker):
        """Test that circuit breaker blocks calls when open."""
        async def fail_func():
            raise ValueError("Test error")
        
        # Trigger failures to open circuit
        for _ in range(2):
            with pytest.raises(ValueError):
                await circuit_breaker.call(fail_func)
        
        assert circuit_breaker.state == CircuitBreakerState.OPEN
        
        # Now calls should be blocked
        async def any_func():
            return "should not execute"
        
        with pytest.raises(CircuitBreakerOpenError):
            await circuit_breaker.call(any_func)
    
    @pytest.mark.asyncio
    async def test_circuit_transitions_to_half_open(self, circuit_breaker):
        """Test transition from open to half-open after timeout."""
        async def fail_func():
            raise ValueError("Test error")
        
        # Open the circuit
        for _ in range(2):
            with pytest.raises(ValueError):
                await circuit_breaker.call(fail_func)
        
        assert circuit_breaker.state == CircuitBreakerState.OPEN
        
        # Wait for recovery timeout
        await asyncio.sleep(1.1)  # Slightly more than recovery_timeout
        
        # Next call should transition to half-open
        async def success_func():
            return "success"
        
        result = await circuit_breaker.call(success_func)
        assert result == "success"
        assert circuit_breaker.state == CircuitBreakerState.HALF_OPEN
    
    @pytest.mark.asyncio
    async def test_half_open_to_closed_transition(self, circuit_breaker):
        """Test transition from half-open to closed after successes."""
        # First, open the circuit
        async def fail_func():
            raise ValueError("Test error")
        
        for _ in range(2):
            with pytest.raises(ValueError):
                await circuit_breaker.call(fail_func)
        
        # Wait and transition to half-open
        await asyncio.sleep(1.1)
        
        async def success_func():
            return "success"
        
        # First success in half-open
        await circuit_breaker.call(success_func)
        assert circuit_breaker.state == CircuitBreakerState.HALF_OPEN
        
        # Second success should close the circuit
        await circuit_breaker.call(success_func)
        assert circuit_breaker.state == CircuitBreakerState.CLOSED
        assert circuit_breaker.failure_count == 0
    
    @pytest.mark.asyncio
    async def test_half_open_to_open_on_failure(self, circuit_breaker):
        """Test that failure in half-open state reopens circuit."""
        # Open the circuit
        async def fail_func():
            raise ValueError("Test error")
        
        for _ in range(2):
            with pytest.raises(ValueError):
                await circuit_breaker.call(fail_func)
        
        # Wait and transition to half-open
        await asyncio.sleep(1.1)
        
        async def success_func():
            return "success"
        
        # One success to get to half-open
        await circuit_breaker.call(success_func)
        assert circuit_breaker.state == CircuitBreakerState.HALF_OPEN
        
        # Failure should reopen circuit
        with pytest.raises(ValueError):
            await circuit_breaker.call(fail_func)
        assert circuit_breaker.state == CircuitBreakerState.OPEN
    
    @pytest.mark.asyncio
    async def test_timeout_handling(self):
        """Test that circuit breaker handles timeouts."""
        config = CircuitBreakerConfig(timeout=0.1)  # Very short timeout
        circuit_breaker = CircuitBreaker("timeout_test", config)
        
        async def slow_func():
            await asyncio.sleep(0.2)  # Longer than timeout
            return "should not complete"
        
        with pytest.raises(asyncio.TimeoutError):
            await circuit_breaker.call(slow_func)
        
        assert circuit_breaker.failure_count == 1
    
    @pytest.mark.asyncio
    async def test_sync_function_call(self, circuit_breaker):
        """Test calling synchronous functions through circuit breaker."""
        def sync_func():
            return "sync_result"
        
        result = await circuit_breaker.call(sync_func)
        assert result == "sync_result"
    
    def test_get_stats(self, circuit_breaker):
        """Test getting circuit breaker statistics."""
        stats = circuit_breaker.get_stats()
        
        assert stats["name"] == "test_breaker"
        assert stats["state"] == CircuitBreakerState.CLOSED.value
        assert stats["failure_count"] == 0
        assert stats["success_count"] == 0
        assert "config" in stats
        assert stats["config"]["failure_threshold"] == 2
    
    @pytest.mark.asyncio
    async def test_reset(self, circuit_breaker):
        """Test resetting circuit breaker."""
        # Open the circuit
        async def fail_func():
            raise ValueError("Test error")
        
        for _ in range(2):
            with pytest.raises(ValueError):
                await circuit_breaker.call(fail_func)
        
        assert circuit_breaker.state == CircuitBreakerState.OPEN
        
        # Reset the circuit breaker
        await circuit_breaker.reset()
        
        assert circuit_breaker.state == CircuitBreakerState.CLOSED
        assert circuit_breaker.failure_count == 0
        assert circuit_breaker.success_count == 0
        assert circuit_breaker.last_failure_time is None


class TestCircuitBreakerManager:
    """Test CircuitBreakerManager class."""
    
    @pytest.fixture
    def manager(self):
        """Create a circuit breaker manager for testing."""
        return CircuitBreakerManager()
    
    def test_get_breaker_creates_new(self, manager):
        """Test that get_breaker creates new circuit breaker."""
        breaker = manager.get_breaker("test_breaker")
        assert isinstance(breaker, CircuitBreaker)
        assert breaker.name == "test_breaker"
    
    def test_get_breaker_returns_existing(self, manager):
        """Test that get_breaker returns existing circuit breaker."""
        breaker1 = manager.get_breaker("test_breaker")
        breaker2 = manager.get_breaker("test_breaker")
        assert breaker1 is breaker2
    
    def test_get_breaker_with_custom_config(self, manager):
        """Test creating circuit breaker with custom config."""
        config = CircuitBreakerConfig(failure_threshold=10)
        breaker = manager.get_breaker("custom_breaker", config)
        assert breaker.config.failure_threshold == 10
    
    def test_get_all_stats(self, manager):
        """Test getting statistics for all circuit breakers."""
        manager.get_breaker("breaker1")
        manager.get_breaker("breaker2")
        
        stats = manager.get_all_stats()
        assert "breaker1" in stats
        assert "breaker2" in stats
        assert len(stats) == 2
    
    @pytest.mark.asyncio
    async def test_reset_all(self, manager):
        """Test resetting all circuit breakers."""
        breaker1 = manager.get_breaker("breaker1")
        breaker2 = manager.get_breaker("breaker2")
        
        # Modify state
        breaker1.failure_count = 5
        breaker2.failure_count = 3
        
        await manager.reset_all()
        
        assert breaker1.failure_count == 0
        assert breaker2.failure_count == 0
    
    @pytest.mark.asyncio
    async def test_reset_specific_breaker(self, manager):
        """Test resetting a specific circuit breaker."""
        breaker1 = manager.get_breaker("breaker1")
        breaker2 = manager.get_breaker("breaker2")
        
        # Modify state
        breaker1.failure_count = 5
        breaker2.failure_count = 3
        
        await manager.reset_breaker("breaker1")
        
        assert breaker1.failure_count == 0
        assert breaker2.failure_count == 3  # Should not be reset
    
    @pytest.mark.asyncio
    async def test_reset_nonexistent_breaker(self, manager):
        """Test resetting a non-existent circuit breaker."""
        # Should not raise an error
        await manager.reset_breaker("nonexistent")
    
    def test_set_default_config(self, manager):
        """Test setting default configuration."""
        config = CircuitBreakerConfig(failure_threshold=15)
        manager.set_default_config(config)
        
        # New breakers should use the default config
        breaker = manager.get_breaker("new_breaker")
        assert breaker.config.failure_threshold == 15


if __name__ == "__main__":
    pytest.main([__file__])