"""
Model registry for managing model types and factory patterns.
"""

import logging
from typing import Dict, List, Callable, Type

from .base_model import BaseModel
from .data_models import ModelType, ModelConfig
from .exceptions import ModelNotFoundError, ConfigurationError

log = logging.getLogger(__name__)

# Type alias for model factory functions
ModelFactory = Callable[[str, ModelConfig], BaseModel]


class ModelRegistry:
    """
    Registry for model types and their factory functions.
    
    This class manages the registration of different model types and provides
    a factory pattern for creating model instances.
    """
    
    def __init__(self):
        """Initialize the model registry."""
        self._model_factories: Dict[ModelType, ModelFactory] = {}
        self._fallback_configs: Dict[ModelType, List[ModelConfig]] = {}
    
    def register_model_type(self, model_type: ModelType, factory: ModelFactory) -> None:
        """
        Register a model type with its factory function.
        
        Args:
            model_type: The type of model to register
            factory: Factory function that creates instances of this model type
        
        Raises:
            ConfigurationError: If the model type is already registered
        """
        if model_type in self._model_factories:
            raise ConfigurationError(f"Model type {model_type} is already registered")
        
        self._model_factories[model_type] = factory
        log.info(f"Registered model type: {model_type}")
    
    def unregister_model_type(self, model_type: ModelType) -> None:
        """
        Unregister a model type.
        
        Args:
            model_type: The type of model to unregister
        """
        if model_type in self._model_factories:
            del self._model_factories[model_type]
            log.info(f"Unregistered model type: {model_type}")
    
    def create_model(self, model_type: ModelType, model_id: str, config: ModelConfig) -> BaseModel:
        """
        Create a model instance using the registered factory.
        
        Args:
            model_type: The type of model to create
            model_id: Unique identifier for the model instance
            config: Configuration for the model
        
        Returns:
            BaseModel instance
        
        Raises:
            ModelNotFoundError: If the model type is not registered
            ConfigurationError: If the configuration is invalid
        """
        if model_type not in self._model_factories:
            raise ModelNotFoundError(f"Model type {model_type} is not registered")
        
        if config.model_type != model_type:
            raise ConfigurationError(
                f"Config model type {config.model_type} does not match requested type {model_type}"
            )
        
        factory = self._model_factories[model_type]
        try:
            model = factory(model_id, config)
            log.info(f"Created model instance: {model_id} of type {model_type}")
            return model
        except Exception as e:
            log.error(f"Failed to create model {model_id} of type {model_type}: {e}")
            raise ConfigurationError(f"Failed to create model: {e}") from e
    
    def register_fallback_configs(self, model_type: ModelType, fallback_configs: List[ModelConfig]) -> None:
        """
        Register fallback configurations for a model type.
        
        Args:
            model_type: The model type to register fallbacks for
            fallback_configs: List of fallback configurations
        """
        self._fallback_configs[model_type] = fallback_configs
        log.info(f"Registered {len(fallback_configs)} fallback configs for {model_type}")
    
    def get_fallback_configs(self, model_type: ModelType) -> List[ModelConfig]:
        """
        Get fallback configurations for a model type.
        
        Args:
            model_type: The model type to get fallbacks for
        
        Returns:
            List of fallback configurations
        """
        return self._fallback_configs.get(model_type, [])
    
    def get_registered_types(self) -> List[ModelType]:
        """
        Get all registered model types.
        
        Returns:
            List of registered model types
        """
        return list(self._model_factories.keys())
    
    def is_registered(self, model_type: ModelType) -> bool:
        """
        Check if a model type is registered.
        
        Args:
            model_type: The model type to check
        
        Returns:
            True if the model type is registered, False otherwise
        """
        return model_type in self._model_factories