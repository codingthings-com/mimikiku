"""
Custom exceptions for the model management system.
"""

from typing import Optional, Any


class ModelManagementError(Exception):
    """Base exception for model management errors."""
    
    def __init__(self, message: str, model_id: Optional[str] = None, 
                 model_type: Optional[str] = None, details: Optional[Any] = None):
        """
        Initialize the exception.
        
        Args:
            message: Error message
            model_id: ID of the model that caused the error
            model_type: Type of the model that caused the error
            details: Additional error details
        """
        super().__init__(message)
        self.model_id = model_id
        self.model_type = model_type
        self.details = details


class ModelLoadError(ModelManagementError):
    """Raised when a model fails to load."""
    pass


class ModelUnloadError(ModelManagementError):
    """Raised when a model fails to unload properly."""
    pass


class InsufficientResourcesError(ModelManagementError):
    """Raised when system lacks required resources."""
    pass


class ModelNotFoundError(ModelManagementError):
    """Raised when a requested model doesn't exist."""
    pass


class ConfigurationError(ModelManagementError):
    """Raised for invalid configurations."""
    pass


class CircuitBreakerOpenError(ModelManagementError):
    """Raised when circuit breaker is open and blocking operations."""
    pass


class RetryExhaustedError(ModelManagementError):
    """Raised when all retry attempts have been exhausted."""
    
    def __init__(self, message: str, attempts: int, last_error: Exception, 
                 model_id: Optional[str] = None, model_type: Optional[str] = None):
        """
        Initialize the retry exhausted exception.
        
        Args:
            message: Error message
            attempts: Number of attempts made
            last_error: The last error that occurred
            model_id: ID of the model that caused the error
            model_type: Type of the model that caused the error
        """
        super().__init__(message, model_id, model_type)
        self.attempts = attempts
        self.last_error = last_error


class FallbackExhaustedError(ModelManagementError):
    """Raised when all fallback models have failed."""
    
    def __init__(self, message: str, failed_models: list, 
                 model_id: Optional[str] = None, model_type: Optional[str] = None):
        """
        Initialize the fallback exhausted exception.
        
        Args:
            message: Error message
            failed_models: List of models that failed
            model_id: ID of the primary model that was requested
            model_type: Type of the model that was requested
        """
        super().__init__(message, model_id, model_type)
        self.failed_models = failed_models


class ModelTimeoutError(ModelManagementError):
    """Raised when model operations timeout."""
    pass


class ResourceConstraintError(ModelManagementError):
    """Raised when resource constraints prevent model operations."""
    pass