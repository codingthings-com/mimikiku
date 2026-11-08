"""
Unit tests for fallback manager implementation.
"""

import pytest
from unittest.mock import AsyncMock, Mock
import time

from server.model_management.fallback_manager import (
    FallbackManager,
    FallbackAttempt
)
from server.model_management.data_models import ModelConfig, ModelType
from server.model_management.exceptions import FallbackExhaustedError, ModelLoadError
from server.model_management.base_model import BaseModel


class MockModel(BaseModel):
    """Mock model for testing."""
    
    def __init__(self, model_id: str, config: ModelConfig, should_fail: bool = False):
        super().__init__(model_id, config)
        self.should_fail = should_fail
        self._loaded = False
    
    async def load(self) -> None:
        if self.should_fail:
            raise ModelLoadError(f"Failed to load {self.config.model_name}")
        self._loaded = True
    
    async def unload(self) -> None:
        self._loaded = False
    
    def is_loaded(self) -> bool:
        return self._loaded
    
    def get_memory_usage(self):
        from server.model_management.data_models import MemoryUsage
        return MemoryUsage(total_mb=1000, used_mb=500, available_mb=500)


class TestFallbackAttempt:
    """Test FallbackAttempt dataclass."""
    
    def test_fallback_attempt_creation(self):
        """Test creating a FallbackAttempt."""
        config = ModelConfig(
            model_type=ModelType.WHISPER_TRANSCRIBER,
            model_name="test_model",
            device="cpu"
        )
        error = ValueError("Test error")
        attempt_time = time.time()
        
        attempt = FallbackAttempt(
            config=config,
            error=error,
            attempt_time=attempt_time
        )
        
        assert attempt.config == config
        assert attempt.error == error
        assert attempt.attempt_time == attempt_time


class TestFallbackManager:
    """Test FallbackManager class."""
    
    @pytest.fixture
    def fallback_manager(self):
        """Create a fallback manager for testing."""
        return FallbackManager()
    
    @pytest.fixture
    def primary_config(self):
        """Create a primary model configuration."""
        return ModelConfig(
            model_type=ModelType.WHISPER_TRANSCRIBER,
            model_name="whisper-large",
            device="cuda"
        )
    
    @pytest.fixture
    def fallback_configs(self):
        """Create fallback configurations."""
        return [
            ModelConfig(
                model_type=ModelType.WHISPER_TRANSCRIBER,
                model_name="whisper-medium",
                device="cuda"
            ),
            ModelConfig(
                model_type=ModelType.WHISPER_TRANSCRIBER,
                model_name="whisper-base",
                device="cpu"
            )
        ]
    
    def test_register_fallback_chain(self, fallback_manager, fallback_configs):
        """Test registering a fallback chain."""
        primary_key = "whisper_transcriber_whisper-large_cuda"
        fallback_manager.register_fallback_chain(primary_key, fallback_configs)
        
        assert primary_key in fallback_manager._fallback_chains
        assert len(fallback_manager._fallback_chains[primary_key]) == 2
    
    def test_get_fallback_chain_with_config_fallbacks(self, fallback_manager, primary_config, fallback_configs):
        """Test getting fallback chain with config-defined fallbacks."""
        primary_config.fallback_configs = fallback_configs
        
        chain = fallback_manager.get_fallback_chain(primary_config)
        
        # Should include primary + config fallbacks + default fallbacks
        assert len(chain) >= 3  # At least primary + 2 config fallbacks
        assert chain[0] == primary_config
        assert chain[1] == fallback_configs[0]
        assert chain[2] == fallback_configs[1]
    
    def test_get_fallback_chain_with_registered_fallbacks(self, fallback_manager, primary_config, fallback_configs):
        """Test getting fallback chain with registered fallbacks."""
        primary_key = "whisper_transcriber_whisper-large_cuda"
        fallback_manager.register_fallback_chain(primary_key, fallback_configs)
        
        chain = fallback_manager.get_fallback_chain(primary_config)
        
        # Should include primary + registered fallbacks + default fallbacks
        assert len(chain) >= 3
        assert chain[0] == primary_config
        # Registered fallbacks should be included
        assert any(config.model_name == "whisper-medium" for config in chain)
    
    def test_get_fallback_chain_default_whisper(self, fallback_manager):
        """Test default fallback chain for Whisper models."""
        config = ModelConfig(
            model_type=ModelType.WHISPER_TRANSCRIBER,
            model_name="custom-whisper",
            device="cuda"
        )
        
        chain = fallback_manager.get_fallback_chain(config)
        
        # Should include primary + default fallbacks
        assert len(chain) > 1
        assert chain[0] == config
        
        # Should include various default Whisper models
        model_names = [c.model_name for c in chain]
        assert any("whisper-large" in name for name in model_names)
        assert any("whisper-base" in name for name in model_names)
    
    def test_get_fallback_chain_default_diarization(self, fallback_manager):
        """Test default fallback chain for diarization models."""
        config = ModelConfig(
            model_type=ModelType.SPEAKER_DIARIZATION,
            model_name="custom-diarization",
            device="cuda"
        )
        
        chain = fallback_manager.get_fallback_chain(config)
        
        # Should include primary + default fallbacks
        assert len(chain) > 1
        assert chain[0] == config
        
        # Should include default diarization models
        model_names = [c.model_name for c in chain]
        assert any("speaker-diarization" in name for name in model_names)
    
    @pytest.mark.asyncio
    async def test_try_fallback_chain_success_primary(self, fallback_manager, primary_config):
        """Test successful loading with primary model."""
        async def model_factory(model_type, model_id, config):
            model = MockModel(model_id, config, should_fail=False)
            await model.load()
            return model
        
        model = await fallback_manager.try_fallback_chain(
            primary_config,
            model_factory,
            "test_model_id"
        )
        
        assert isinstance(model, MockModel)
        assert model.is_loaded()
        assert model.config == primary_config
    
    @pytest.mark.asyncio
    async def test_try_fallback_chain_success_fallback(self, fallback_manager, primary_config, fallback_configs):
        """Test successful loading with fallback model."""
        primary_config.fallback_configs = fallback_configs
        
        call_count = 0
        
        async def model_factory(model_type, model_id, config):
            nonlocal call_count
            call_count += 1
            # First call (primary) fails, second call (fallback) succeeds
            should_fail = call_count == 1
            model = MockModel(model_id, config, should_fail=should_fail)
            # The fallback manager expects the factory to handle loading
            if should_fail:
                await model.load()  # This will raise an exception
            else:
                await model.load()  # This will succeed
            return model
        
        model = await fallback_manager.try_fallback_chain(
            primary_config,
            model_factory,
            "test_model_id"
        )
        
        assert isinstance(model, MockModel)
        assert model.is_loaded()
        assert model.config == fallback_configs[0]  # Should be first fallback
        assert call_count == 2  # Primary + first fallback
    
    @pytest.mark.asyncio
    async def test_try_fallback_chain_all_fail(self, fallback_manager, primary_config, fallback_configs):
        """Test when all models in fallback chain fail."""
        primary_config.fallback_configs = fallback_configs
        
        async def model_factory(model_type, model_id, config):
            model = MockModel(model_id, config, should_fail=True)
            # Call load() which will fail and raise an exception
            await model.load()
            return model
        
        with pytest.raises(FallbackExhaustedError) as exc_info:
            await fallback_manager.try_fallback_chain(
                primary_config,
                model_factory,
                "test_model_id"
            )
        
        error = exc_info.value
        assert error.model_id == "test_model_id"
        assert error.model_type == ModelType.WHISPER_TRANSCRIBER.value
        assert len(error.failed_models) >= 3  # Primary + fallbacks + defaults
    
    def test_get_failed_attempts_all(self, fallback_manager):
        """Test getting all failed attempts."""
        # Add some failed attempts
        fallback_manager._failed_attempts["model1"] = [
            FallbackAttempt(
                config=ModelConfig(ModelType.WHISPER_TRANSCRIBER, "test", "cpu"),
                error=ValueError("Error 1"),
                attempt_time=time.time()
            )
        ]
        fallback_manager._failed_attempts["model2"] = [
            FallbackAttempt(
                config=ModelConfig(ModelType.WHISPER_TRANSCRIBER, "test2", "cpu"),
                error=ValueError("Error 2"),
                attempt_time=time.time()
            )
        ]
        
        attempts = fallback_manager.get_failed_attempts()
        assert "model1" in attempts
        assert "model2" in attempts
        assert len(attempts) == 2
    
    def test_get_failed_attempts_specific(self, fallback_manager):
        """Test getting failed attempts for specific model."""
        # Add some failed attempts
        fallback_manager._failed_attempts["model1"] = [
            FallbackAttempt(
                config=ModelConfig(ModelType.WHISPER_TRANSCRIBER, "test", "cpu"),
                error=ValueError("Error 1"),
                attempt_time=time.time()
            )
        ]
        
        attempts = fallback_manager.get_failed_attempts("model1")
        assert "model1" in attempts
        assert len(attempts) == 1
        assert len(attempts["model1"]) == 1
    
    def test_clear_failed_attempts_all(self, fallback_manager):
        """Test clearing all failed attempts."""
        # Add some failed attempts
        fallback_manager._failed_attempts["model1"] = [Mock()]
        fallback_manager._failed_attempts["model2"] = [Mock()]
        
        fallback_manager.clear_failed_attempts()
        
        assert len(fallback_manager._failed_attempts) == 0
    
    def test_clear_failed_attempts_specific(self, fallback_manager):
        """Test clearing failed attempts for specific model."""
        # Add some failed attempts
        fallback_manager._failed_attempts["model1"] = [Mock()]
        fallback_manager._failed_attempts["model2"] = [Mock()]
        
        fallback_manager.clear_failed_attempts("model1")
        
        assert "model1" not in fallback_manager._failed_attempts
        assert "model2" in fallback_manager._failed_attempts
    
    def test_create_model_key(self, fallback_manager):
        """Test creating model key from configuration."""
        config = ModelConfig(
            model_type=ModelType.WHISPER_TRANSCRIBER,
            model_name="whisper-large",
            device="cuda"
        )
        
        key = fallback_manager._create_model_key(config)
        assert key == "whisper_transcriber_whisper-large_cuda"
    
    def test_get_default_fallbacks_whisper(self, fallback_manager):
        """Test getting default fallbacks for Whisper models."""
        fallbacks = fallback_manager._get_default_fallbacks(ModelType.WHISPER_TRANSCRIBER)
        
        assert len(fallbacks) > 0
        assert all(config.model_type == ModelType.WHISPER_TRANSCRIBER for config in fallbacks)
        
        # Should include various model sizes and devices
        model_names = [config.model_name for config in fallbacks]
        devices = [config.device for config in fallbacks]
        
        assert any("large" in name for name in model_names)
        assert any("base" in name for name in model_names)
        assert any("tiny" in name for name in model_names)
        assert "cuda" in devices
        assert "cpu" in devices
    
    def test_get_default_fallbacks_diarization(self, fallback_manager):
        """Test getting default fallbacks for diarization models."""
        fallbacks = fallback_manager._get_default_fallbacks(ModelType.SPEAKER_DIARIZATION)
        
        assert len(fallbacks) > 0
        assert all(config.model_type == ModelType.SPEAKER_DIARIZATION for config in fallbacks)
        
        # Should include various versions and devices
        model_names = [config.model_name for config in fallbacks]
        devices = [config.device for config in fallbacks]
        
        assert any("speaker-diarization" in name for name in model_names)
        assert "cuda" in devices
        assert "cpu" in devices
    
    def test_get_statistics(self, fallback_manager, fallback_configs):
        """Test getting fallback statistics."""
        # Register some chains
        fallback_manager.register_fallback_chain("chain1", fallback_configs)
        
        # Add some failed attempts
        fallback_manager._failed_attempts["model1"] = [
            FallbackAttempt(
                config=ModelConfig(ModelType.WHISPER_TRANSCRIBER, "test", "cpu"),
                error=ValueError("Error"),
                attempt_time=time.time()
            )
        ]
        
        stats = fallback_manager.get_statistics()
        
        assert stats["registered_chains"] == 1
        assert stats["models_with_failures"] == 1
        assert stats["total_failed_attempts"] == 1
        assert "fallback_chains" in stats
        assert "failure_history" in stats
        
        # Check chain details
        assert "chain1" in stats["fallback_chains"]
        assert stats["fallback_chains"]["chain1"]["fallback_count"] == 2
        
        # Check failure details
        assert "model1" in stats["failure_history"]
        assert stats["failure_history"]["model1"]["failure_count"] == 1


if __name__ == "__main__":
    pytest.main([__file__])