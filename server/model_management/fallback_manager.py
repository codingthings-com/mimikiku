"""
Fallback manager for handling model fallback strategies.
"""

import logging
from typing import List, Optional, Dict, Any
from dataclasses import dataclass

from .data_models import ModelConfig, ModelType
from .exceptions import FallbackExhaustedError, ModelLoadError
from .base_model import BaseModel

log = logging.getLogger(__name__)


@dataclass
class FallbackAttempt:
    """Record of a fallback attempt."""
    config: ModelConfig
    error: Exception
    attempt_time: float


class FallbackManager:
    """
    Manages fallback strategies for model loading failures.
    
    This class handles the logic for trying alternative models when
    the primary model fails to load, with configurable fallback chains.
    """
    
    def __init__(self):
        """Initialize the fallback manager."""
        self._fallback_chains: Dict[str, List[ModelConfig]] = {}
        self._failed_attempts: Dict[str, List[FallbackAttempt]] = {}
    
    def register_fallback_chain(self, primary_model_key: str, fallback_configs: List[ModelConfig]) -> None:
        """
        Register a fallback chain for a primary model.
        
        Args:
            primary_model_key: Key identifying the primary model
            fallback_configs: List of fallback model configurations in priority order
        """
        self._fallback_chains[primary_model_key] = fallback_configs.copy()
        log.info(f"Registered fallback chain for '{primary_model_key}' with {len(fallback_configs)} fallbacks")
    
    def get_fallback_chain(self, primary_config: ModelConfig) -> List[ModelConfig]:
        """
        Get the fallback chain for a primary model configuration.
        
        Args:
            primary_config: Primary model configuration
        
        Returns:
            List of fallback configurations, including the primary config first
        """
        # Create a key for the primary model
        primary_key = self._create_model_key(primary_config)
        
        # Start with the primary config
        chain = [primary_config]
        
        # Add configured fallbacks
        if primary_config.fallback_configs:
            chain.extend(primary_config.fallback_configs)
        
        # Add registered fallbacks
        if primary_key in self._fallback_chains:
            chain.extend(self._fallback_chains[primary_key])
        
        # Add default fallbacks based on model type
        default_fallbacks = self._get_default_fallbacks(primary_config.model_type)
        for fallback in default_fallbacks:
            if fallback not in chain:
                chain.append(fallback)
        
        return chain
    
    async def try_fallback_chain(
        self, 
        primary_config: ModelConfig, 
        model_factory: callable,
        model_id: str
    ) -> BaseModel:
        """
        Try to load a model using the fallback chain.
        
        Args:
            primary_config: Primary model configuration
            model_factory: Factory function to create model instances
            model_id: ID for the model instance
        
        Returns:
            Successfully loaded model instance
        
        Raises:
            FallbackExhaustedError: If all fallback attempts fail
        """
        fallback_chain = self.get_fallback_chain(primary_config)
        failed_attempts = []
        primary_key = self._create_model_key(primary_config)
        
        log.info(f"Trying fallback chain for '{primary_key}' with {len(fallback_chain)} options")
        
        for i, config in enumerate(fallback_chain):
            try:
                log.debug(f"Attempting to load model {i+1}/{len(fallback_chain)}: {config.model_name}")
                
                # Create and load the model
                model = await model_factory(config.model_type, model_id, config)
                
                if i > 0:
                    log.warning(f"Successfully loaded fallback model '{config.model_name}' "
                              f"after {i} failed attempts")
                else:
                    log.debug(f"Successfully loaded primary model '{config.model_name}'")
                
                # Clear failed attempts on success
                if primary_key in self._failed_attempts:
                    del self._failed_attempts[primary_key]
                
                return model
                
            except Exception as e:
                error_msg = f"Failed to load model '{config.model_name}': {e}"
                log.warning(error_msg)
                
                # Record the failed attempt
                failed_attempts.append(FallbackAttempt(
                    config=config,
                    error=e,
                    attempt_time=__import__('time').time()
                ))
        
        # All fallbacks failed
        self._failed_attempts[primary_key] = failed_attempts
        
        failed_models = [attempt.config.model_name for attempt in failed_attempts]
        error_msg = (
            f"All fallback models failed for '{primary_key}'. "
            f"Tried: {', '.join(failed_models)}"
        )
        
        raise FallbackExhaustedError(
            error_msg,
            failed_models=failed_models,
            model_id=model_id,
            model_type=primary_config.model_type.value
        )
    
    def get_failed_attempts(self, model_key: Optional[str] = None) -> Dict[str, List[FallbackAttempt]]:
        """
        Get failed attempt history.
        
        Args:
            model_key: Specific model key to get attempts for (all if None)
        
        Returns:
            Dictionary of failed attempts by model key
        """
        if model_key:
            return {model_key: self._failed_attempts.get(model_key, [])}
        return self._failed_attempts.copy()
    
    def clear_failed_attempts(self, model_key: Optional[str] = None) -> None:
        """
        Clear failed attempt history.
        
        Args:
            model_key: Specific model key to clear (all if None)
        """
        if model_key:
            if model_key in self._failed_attempts:
                del self._failed_attempts[model_key]
                log.info(f"Cleared failed attempts for '{model_key}'")
        else:
            self._failed_attempts.clear()
            log.info("Cleared all failed attempts")
    
    def _create_model_key(self, config: ModelConfig) -> str:
        """
        Create a unique key for a model configuration.
        
        Args:
            config: Model configuration
        
        Returns:
            Unique key string
        """
        return f"{config.model_type.value}_{config.model_name}_{config.device}"
    
    def _get_default_fallbacks(self, model_type: ModelType) -> List[ModelConfig]:
        """
        Get default fallback configurations for a model type.
        
        Args:
            model_type: Type of model
        
        Returns:
            List of default fallback configurations
        """
        fallbacks = []
        
        if model_type == ModelType.WHISPER_TRANSCRIBER:
            # Default Whisper fallbacks: larger models -> smaller models, GPU -> CPU
            fallback_models = [
                ("openai/whisper-large-v3", "cuda"),
                ("openai/whisper-large-v2", "cuda"),
                ("openai/whisper-medium", "cuda"),
                ("openai/whisper-base", "cuda"),
                ("openai/whisper-small", "cuda"),
                ("openai/whisper-tiny", "cuda"),
                ("openai/whisper-base", "cpu"),
                ("openai/whisper-small", "cpu"),
                ("openai/whisper-tiny", "cpu"),
            ]
            
            for model_name, device in fallback_models:
                fallbacks.append(ModelConfig(
                    model_type=model_type,
                    model_name=model_name,
                    device=device,
                    ttl_seconds=300,
                    max_memory_mb=4096 if "large" not in model_name else 8192,
                    priority=1
                ))
        
        elif model_type == ModelType.SPEAKER_DIARIZATION:
            # Default diarization fallbacks: different models and devices
            fallback_models = [
                ("pyannote/speaker-diarization-3.1", "cuda"),
                ("pyannote/speaker-diarization-3.0", "cuda"),
                ("pyannote/speaker-diarization-3.1", "cpu"),
                ("pyannote/speaker-diarization-3.0", "cpu"),
            ]
            
            for model_name, device in fallback_models:
                fallbacks.append(ModelConfig(
                    model_type=model_type,
                    model_name=model_name,
                    device=device,
                    ttl_seconds=300,
                    max_memory_mb=2048,
                    priority=1
                ))
        
        return fallbacks
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        Get statistics about fallback usage.
        
        Returns:
            Dictionary containing fallback statistics
        """
        stats = {
            "registered_chains": len(self._fallback_chains),
            "models_with_failures": len(self._failed_attempts),
            "total_failed_attempts": sum(len(attempts) for attempts in self._failed_attempts.values()),
            "fallback_chains": {}
        }
        
        # Add details about each fallback chain
        for key, chain in self._fallback_chains.items():
            stats["fallback_chains"][key] = {
                "fallback_count": len(chain),
                "fallback_models": [config.model_name for config in chain]
            }
        
        # Add failure statistics
        failure_stats = {}
        for key, attempts in self._failed_attempts.items():
            failure_stats[key] = {
                "failure_count": len(attempts),
                "last_failure": max(attempt.attempt_time for attempt in attempts) if attempts else None,
                "failed_models": [attempt.config.model_name for attempt in attempts]
            }
        
        stats["failure_history"] = failure_stats
        
        return stats