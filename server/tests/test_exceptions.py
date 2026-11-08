"""
Unit tests for model management exceptions.
"""

import pytest
from server.model_management.exceptions import (
    ModelManagementError,
    ModelLoadError,
    ModelUnloadError,
    InsufficientResourcesError,
    ModelNotFoundError,
    ConfigurationError,
    CircuitBreakerOpenError,
    RetryExhaustedError,
    FallbackExhaustedError,
    ModelTimeoutError,
    ResourceConstraintError
)


class TestModelManagementError:
    """Test the base ModelManagementError class."""
    
    def test_basic_initialization(self):
        """Test basic error initialization."""
        error = ModelManagementError("Test error")
        assert str(error) == "Test error"
        assert error.model_id is None
        assert error.model_type is None
        assert error.details is None
    
    def test_full_initialization(self):
        """Test error initialization with all parameters."""
        details = {"key": "value"}
        error = ModelManagementError(
            "Test error",
            model_id="test_model",
            model_type="whisper",
            details=details
        )
        
        assert str(error) == "Test error"
        assert error.model_id == "test_model"
        assert error.model_type == "whisper"
        assert error.details == details


class TestSpecificExceptions:
    """Test specific exception classes."""
    
    def test_model_load_error(self):
        """Test ModelLoadError."""
        error = ModelLoadError("Failed to load", model_id="test_model")
        assert isinstance(error, ModelManagementError)
        assert str(error) == "Failed to load"
        assert error.model_id == "test_model"
    
    def test_model_unload_error(self):
        """Test ModelUnloadError."""
        error = ModelUnloadError("Failed to unload", model_id="test_model")
        assert isinstance(error, ModelManagementError)
        assert str(error) == "Failed to unload"
    
    def test_insufficient_resources_error(self):
        """Test InsufficientResourcesError."""
        error = InsufficientResourcesError("Not enough memory")
        assert isinstance(error, ModelManagementError)
        assert str(error) == "Not enough memory"
    
    def test_model_not_found_error(self):
        """Test ModelNotFoundError."""
        error = ModelNotFoundError("Model not found", model_type="whisper")
        assert isinstance(error, ModelManagementError)
        assert error.model_type == "whisper"
    
    def test_configuration_error(self):
        """Test ConfigurationError."""
        error = ConfigurationError("Invalid config")
        assert isinstance(error, ModelManagementError)
        assert str(error) == "Invalid config"
    
    def test_circuit_breaker_open_error(self):
        """Test CircuitBreakerOpenError."""
        error = CircuitBreakerOpenError("Circuit breaker is open")
        assert isinstance(error, ModelManagementError)
        assert str(error) == "Circuit breaker is open"
    
    def test_model_timeout_error(self):
        """Test ModelTimeoutError."""
        error = ModelTimeoutError("Operation timed out")
        assert isinstance(error, ModelManagementError)
        assert str(error) == "Operation timed out"
    
    def test_resource_constraint_error(self):
        """Test ResourceConstraintError."""
        error = ResourceConstraintError("Resource constraints violated")
        assert isinstance(error, ModelManagementError)
        assert str(error) == "Resource constraints violated"


class TestRetryExhaustedError:
    """Test RetryExhaustedError class."""
    
    def test_initialization(self):
        """Test RetryExhaustedError initialization."""
        last_error = ValueError("Original error")
        error = RetryExhaustedError(
            "All retries failed",
            attempts=3,
            last_error=last_error,
            model_id="test_model",
            model_type="whisper"
        )
        
        assert isinstance(error, ModelManagementError)
        assert str(error) == "All retries failed"
        assert error.attempts == 3
        assert error.last_error == last_error
        assert error.model_id == "test_model"
        assert error.model_type == "whisper"


class TestFallbackExhaustedError:
    """Test FallbackExhaustedError class."""
    
    def test_initialization(self):
        """Test FallbackExhaustedError initialization."""
        failed_models = ["model1", "model2", "model3"]
        error = FallbackExhaustedError(
            "All fallbacks failed",
            failed_models=failed_models,
            model_id="primary_model",
            model_type="whisper"
        )
        
        assert isinstance(error, ModelManagementError)
        assert str(error) == "All fallbacks failed"
        assert error.failed_models == failed_models
        assert error.model_id == "primary_model"
        assert error.model_type == "whisper"
    
    def test_empty_failed_models(self):
        """Test with empty failed models list."""
        error = FallbackExhaustedError("No fallbacks", failed_models=[])
        assert error.failed_models == []


class TestExceptionInheritance:
    """Test exception inheritance hierarchy."""
    
    def test_all_inherit_from_base(self):
        """Test that all exceptions inherit from ModelManagementError."""
        # Simple exceptions that only need a message
        simple_exception_classes = [
            ModelLoadError,
            ModelUnloadError,
            InsufficientResourcesError,
            ModelNotFoundError,
            ConfigurationError,
            CircuitBreakerOpenError,
            ModelTimeoutError,
            ResourceConstraintError
        ]
        
        for exc_class in simple_exception_classes:
            error = exc_class("Test message")
            assert isinstance(error, ModelManagementError)
            assert isinstance(error, Exception)
        
        # Test RetryExhaustedError with required parameters
        retry_error = RetryExhaustedError("Test message", 3, ValueError("test"), "model1", "whisper")
        assert isinstance(retry_error, ModelManagementError)
        assert isinstance(retry_error, Exception)
        
        # Test FallbackExhaustedError with required parameters
        fallback_error = FallbackExhaustedError("Test message", ["model1", "model2"], "primary", "whisper")
        assert isinstance(fallback_error, ModelManagementError)
        assert isinstance(fallback_error, Exception)
    
    def test_base_inherits_from_exception(self):
        """Test that base class inherits from Exception."""
        error = ModelManagementError("Test")
        assert isinstance(error, Exception)


if __name__ == "__main__":
    pytest.main([__file__])