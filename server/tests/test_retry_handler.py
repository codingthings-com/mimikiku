"""
Unit tests for retry handler implementation.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, Mock, patch
from datetime import datetime

from server.model_management.retry_handler import (
    RetryHandler,
    RetryConfig,
    RetryManager,
    RETRY_CONFIGS
)
from server.model_management.exceptions import RetryExhaustedError, ModelManagementError


class TestRetryConfig:
    """Test RetryConfig dataclass."""
    
    def test_default_values(self):
        """Test default configuration values."""
        config = RetryConfig()
        assert config.max_attempts == 3
        assert config.base_delay == 1.0
        assert config.max_delay == 60.0
        assert config.exponential_base == 2.0
        assert config.jitter is True
        assert config.retryable_exceptions is not None
    
    def test_custom_values(self):
        """Test custom configuration values."""
        custom_exceptions = [ValueError, TypeError]
        config = RetryConfig(
            max_attempts=5,
            base_delay=2.0,
            max_delay=120.0,
            exponential_base=3.0,
            jitter=False,
            retryable_exceptions=custom_exceptions
        )
        assert config.max_attempts == 5
        assert config.base_delay == 2.0
        assert config.max_delay == 120.0
        assert config.exponential_base == 3.0
        assert config.jitter is False
        assert config.retryable_exceptions == custom_exceptions


class TestRetryHandler:
    """Test RetryHandler class."""
    
    @pytest.fixture
    def config(self):
        """Create a test configuration."""
        return RetryConfig(
            max_attempts=3,
            base_delay=0.1,  # Short delay for fast testing
            max_delay=1.0,
            exponential_base=2.0,
            jitter=False,  # Disable jitter for predictable testing
            retryable_exceptions=[ValueError, ConnectionError]
        )
    
    @pytest.fixture
    def retry_handler(self, config):
        """Create a retry handler for testing."""
        return RetryHandler(config)
    
    @pytest.mark.asyncio
    async def test_successful_call_no_retry(self, retry_handler):
        """Test successful function call without retries."""
        async def success_func():
            return "success"
        
        result = await retry_handler.execute_with_retry(
            success_func,
            operation_name="test_operation"
        )
        assert result == "success"
    
    @pytest.mark.asyncio
    async def test_sync_function_call(self, retry_handler):
        """Test calling synchronous functions."""
        def sync_func():
            return "sync_result"
        
        result = await retry_handler.execute_with_retry(
            sync_func,
            operation_name="sync_test"
        )
        assert result == "sync_result"
    
    @pytest.mark.asyncio
    async def test_retry_on_retryable_exception(self, retry_handler):
        """Test retry behavior on retryable exceptions."""
        call_count = 0
        
        async def flaky_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("Temporary error")
            return "success"
        
        result = await retry_handler.execute_with_retry(
            flaky_func,
            operation_name="flaky_test"
        )
        
        assert result == "success"
        assert call_count == 3
    
    @pytest.mark.asyncio
    async def test_no_retry_on_non_retryable_exception(self, retry_handler):
        """Test that non-retryable exceptions are not retried."""
        call_count = 0
        
        async def fail_func():
            nonlocal call_count
            call_count += 1
            raise TypeError("Non-retryable error")
        
        with pytest.raises(TypeError):
            await retry_handler.execute_with_retry(
                fail_func,
                operation_name="non_retryable_test"
            )
        
        assert call_count == 1  # Should not retry
    
    @pytest.mark.asyncio
    async def test_retry_exhausted_error(self, retry_handler):
        """Test RetryExhaustedError when all attempts fail."""
        call_count = 0
        
        async def always_fail():
            nonlocal call_count
            call_count += 1
            raise ValueError("Always fails")
        
        with pytest.raises(RetryExhaustedError) as exc_info:
            await retry_handler.execute_with_retry(
                always_fail,
                operation_name="always_fail_test",
                model_id="test_model",
                model_type="test_type"
            )
        
        error = exc_info.value
        assert error.attempts == 3
        assert isinstance(error.last_error, ValueError)
        assert error.model_id == "test_model"
        assert error.model_type == "test_type"
        assert call_count == 3
    
    @pytest.mark.asyncio
    async def test_delay_calculation(self, retry_handler):
        """Test delay calculation between retries."""
        delays = []
        
        async def mock_sleep(delay):
            delays.append(delay)
        
        call_count = 0
        
        async def fail_func():
            nonlocal call_count
            call_count += 1
            raise ValueError("Test error")
        
        with patch('asyncio.sleep', mock_sleep):
            with pytest.raises(RetryExhaustedError):
                await retry_handler.execute_with_retry(
                    fail_func,
                    operation_name="delay_test"
                )
        
        # Should have 2 delays (between 3 attempts)
        assert len(delays) == 2
        assert delays[0] == 0.1  # base_delay
        assert delays[1] == 0.2  # base_delay * exponential_base
    
    @pytest.mark.asyncio
    async def test_max_delay_limit(self):
        """Test that delays are capped at max_delay."""
        config = RetryConfig(
            max_attempts=4,
            base_delay=10.0,
            max_delay=15.0,
            exponential_base=2.0,
            jitter=False,
            retryable_exceptions=[ValueError]  # Make ValueError retryable for this test
        )
        retry_handler = RetryHandler(config)
        
        delays = []
        
        async def mock_sleep(delay):
            delays.append(delay)
        
        async def fail_func():
            raise ValueError("Test error")
        
        with patch('asyncio.sleep', mock_sleep):
            with pytest.raises(RetryExhaustedError):
                await retry_handler.execute_with_retry(
                    fail_func,
                    operation_name="max_delay_test"
                )
        
        # Third delay should be capped at max_delay
        assert delays[0] == 10.0  # base_delay
        assert delays[1] == 15.0  # capped at max_delay (would be 20.0)
        assert delays[2] == 15.0  # capped at max_delay (would be 40.0)
    
    @pytest.mark.asyncio
    async def test_jitter_adds_randomness(self):
        """Test that jitter adds randomness to delays."""
        config = RetryConfig(
            max_attempts=3,
            base_delay=1.0,
            jitter=True,
            retryable_exceptions=[ValueError]  # Make ValueError retryable for this test
        )
        retry_handler = RetryHandler(config)
        
        delays = []
        
        async def mock_sleep(delay):
            delays.append(delay)
        
        async def fail_func():
            raise ValueError("Test error")
        
        with patch('asyncio.sleep', mock_sleep):
            with pytest.raises(RetryExhaustedError):
                await retry_handler.execute_with_retry(
                    fail_func,
                    operation_name="jitter_test"
                )
        
        # With jitter, delays should not be exactly the base values
        # (though this test might be flaky due to randomness)
        assert len(delays) == 2
        # Delays should be within reasonable range of expected values
        assert 0.5 <= delays[0] <= 1.5  # base_delay ± 25%
    
    def test_is_retryable_exception(self, retry_handler):
        """Test retryable exception detection."""
        # Configured retryable exceptions
        assert retry_handler._is_retryable_exception(ValueError("test"))
        assert retry_handler._is_retryable_exception(ConnectionError("test"))
        
        # Non-retryable exception
        assert not retry_handler._is_retryable_exception(TypeError("test"))
        
        # Model management errors with retryable keywords
        retryable_error = ModelManagementError("Connection timeout occurred")
        assert retry_handler._is_retryable_exception(retryable_error)
        
        non_retryable_error = ModelManagementError("Invalid configuration")
        assert not retry_handler._is_retryable_exception(non_retryable_error)
    
    def test_calculate_delay(self, retry_handler):
        """Test delay calculation method."""
        # First attempt (attempt=1)
        delay1 = retry_handler._calculate_delay(1)
        assert delay1 == 0.1  # base_delay
        
        # Second attempt (attempt=2)
        delay2 = retry_handler._calculate_delay(2)
        assert delay2 == 0.2  # base_delay * exponential_base
        
        # Third attempt (attempt=3)
        delay3 = retry_handler._calculate_delay(3)
        assert delay3 == 0.4  # base_delay * exponential_base^2
    
    def test_update_config(self, retry_handler):
        """Test updating retry configuration."""
        new_config = RetryConfig(max_attempts=5, base_delay=2.0)
        retry_handler.update_config(new_config)
        
        assert retry_handler.config.max_attempts == 5
        assert retry_handler.config.base_delay == 2.0


class TestRetryManager:
    """Test RetryManager class."""
    
    @pytest.fixture
    def manager(self):
        """Create a retry manager for testing."""
        return RetryManager()
    
    def test_get_handler_creates_new(self, manager):
        """Test that get_handler creates new retry handler."""
        handler = manager.get_handler("test_handler")
        assert isinstance(handler, RetryHandler)
    
    def test_get_handler_returns_existing(self, manager):
        """Test that get_handler returns existing retry handler."""
        handler1 = manager.get_handler("test_handler")
        handler2 = manager.get_handler("test_handler")
        assert handler1 is handler2
    
    def test_get_handler_with_custom_config(self, manager):
        """Test creating retry handler with custom config."""
        config = RetryConfig(max_attempts=10)
        handler = manager.get_handler("custom_handler", config)
        assert handler.config.max_attempts == 10
    
    def test_set_default_config(self, manager):
        """Test setting default configuration."""
        config = RetryConfig(max_attempts=7)
        manager.set_default_config(config)
        
        # New handlers should use the default config
        handler = manager.get_handler("new_handler")
        assert handler.config.max_attempts == 7
    
    def test_get_handler_configs(self, manager):
        """Test getting configurations for all handlers."""
        manager.get_handler("handler1")
        manager.get_handler("handler2")
        
        configs = manager.get_handler_configs()
        assert "handler1" in configs
        assert "handler2" in configs
        assert len(configs) == 2


class TestPredefinedConfigs:
    """Test predefined retry configurations."""
    
    def test_retry_configs_exist(self):
        """Test that predefined configurations exist."""
        assert "model_load" in RETRY_CONFIGS
        assert "model_unload" in RETRY_CONFIGS
        assert "resource_check" in RETRY_CONFIGS
        assert "fallback_switch" in RETRY_CONFIGS
    
    def test_model_load_config(self):
        """Test model load retry configuration."""
        config = RETRY_CONFIGS["model_load"]
        assert config.max_attempts == 3
        assert config.base_delay == 2.0
        assert config.max_delay == 30.0
        assert config.jitter is True
    
    def test_model_unload_config(self):
        """Test model unload retry configuration."""
        config = RETRY_CONFIGS["model_unload"]
        assert config.max_attempts == 2
        assert config.base_delay == 1.0
        assert config.max_delay == 10.0
        assert config.jitter is False
    
    def test_resource_check_config(self):
        """Test resource check retry configuration."""
        config = RETRY_CONFIGS["resource_check"]
        assert config.max_attempts == 5
        assert config.base_delay == 0.5
        assert config.max_delay == 5.0
        assert config.exponential_base == 1.5
    
    def test_fallback_switch_config(self):
        """Test fallback switch retry configuration."""
        config = RETRY_CONFIGS["fallback_switch"]
        assert config.max_attempts == 2
        assert config.base_delay == 1.0
        assert config.max_delay == 5.0
        assert config.jitter is False


if __name__ == "__main__":
    pytest.main([__file__])