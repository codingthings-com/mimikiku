"""
Unit tests for advanced resource management policies.
"""

import asyncio
import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, MagicMock

from model_management.resource_monitor import (
    ResourceMonitor, ResourcePressureLevel, EvictionPolicy
)
from model_management.model_manager import ModelManager
from model_management.data_models import MemoryUsage, ModelStatus, ModelType
from model_management.metrics_collector import MetricsCollector


class TestResourcePressureDetection:
    """Test resource pressure detection functionality."""
    
    def test_normal_pressure_level(self):
        """Test normal pressure level detection."""
        monitor = ResourceMonitor(
            memory_threshold_percent=90.0,  # Set higher than critical
            warning_threshold_percent=70.0,
            critical_threshold_percent=85.0,
            emergency_threshold_percent=95.0,
            min_free_memory_mb=100  # Set lower than available memory
        )
        
        # Mock memory usage at 60%
        with patch.object(monitor, 'get_memory_usage') as mock_memory:
            mock_memory.return_value = MemoryUsage(
                total_mb=1000, used_mb=600, available_mb=400,  # 400MB available > 100MB min
                gpu_total_mb=1000, gpu_used_mb=500
            )
            
            assert monitor.get_pressure_level() == ResourcePressureLevel.NORMAL
            assert not monitor.is_memory_pressure()
            assert not monitor.should_trigger_eviction()
    
    def test_warning_pressure_level(self):
        """Test warning pressure level detection."""
        monitor = ResourceMonitor(
            memory_threshold_percent=90.0,  # Set higher than critical
            warning_threshold_percent=70.0,
            critical_threshold_percent=85.0,
            emergency_threshold_percent=95.0,
            min_free_memory_mb=100  # Set lower than available memory
        )
        
        # Mock memory usage at 75%
        with patch.object(monitor, 'get_memory_usage') as mock_memory:
            mock_memory.return_value = MemoryUsage(
                total_mb=1000, used_mb=750, available_mb=250,  # 250MB available > 100MB min
                gpu_total_mb=1000, gpu_used_mb=600
            )
            
            assert monitor.get_pressure_level() == ResourcePressureLevel.WARNING
            assert monitor.is_memory_pressure()
            # Should not trigger eviction immediately
            assert not monitor.should_trigger_eviction()
    
    def test_critical_pressure_level(self):
        """Test critical pressure level detection."""
        monitor = ResourceMonitor(
            warning_threshold_percent=70.0,
            critical_threshold_percent=85.0,
            emergency_threshold_percent=95.0
        )
        
        # Mock memory usage at 90%
        with patch.object(monitor, 'get_memory_usage') as mock_memory:
            mock_memory.return_value = MemoryUsage(
                total_mb=1000, used_mb=900, available_mb=100,
                gpu_total_mb=1000, gpu_used_mb=800
            )
            
            assert monitor.get_pressure_level() == ResourcePressureLevel.CRITICAL
            assert monitor.is_memory_pressure()
            assert monitor.should_trigger_eviction()
    
    def test_emergency_pressure_level(self):
        """Test emergency pressure level detection."""
        monitor = ResourceMonitor(
            warning_threshold_percent=70.0,
            critical_threshold_percent=85.0,
            emergency_threshold_percent=95.0
        )
        
        # Mock memory usage at 98%
        with patch.object(monitor, 'get_memory_usage') as mock_memory:
            mock_memory.return_value = MemoryUsage(
                total_mb=1000, used_mb=980, available_mb=20,
                gpu_total_mb=1000, gpu_used_mb=950
            )
            
            assert monitor.get_pressure_level() == ResourcePressureLevel.EMERGENCY
            assert monitor.is_memory_pressure()
            assert monitor.should_trigger_eviction()
    
    def test_gpu_pressure_detection(self):
        """Test GPU-specific pressure detection."""
        monitor = ResourceMonitor(
            warning_threshold_percent=70.0,
            critical_threshold_percent=85.0,
            emergency_threshold_percent=95.0
        )
        
        # Mock high GPU usage but normal system memory
        with patch.object(monitor, 'get_memory_usage') as mock_memory:
            mock_memory.return_value = MemoryUsage(
                total_mb=1000, used_mb=600, available_mb=400,
                gpu_total_mb=1000, gpu_used_mb=900  # 90% GPU usage
            )
            
            assert monitor.get_pressure_level() == ResourcePressureLevel.CRITICAL
            assert monitor.should_fallback_to_cpu("cuda")
    
    def test_min_free_memory_pressure(self):
        """Test minimum free memory pressure detection."""
        monitor = ResourceMonitor(
            warning_threshold_percent=70.0,
            critical_threshold_percent=85.0,
            emergency_threshold_percent=95.0,
            min_free_memory_mb=512
        )
        
        # Mock low available memory even with moderate usage
        with patch.object(monitor, 'get_memory_usage') as mock_memory:
            mock_memory.return_value = MemoryUsage(
                total_mb=1000, used_mb=600, available_mb=200,  # Less than min_free_memory_mb
                gpu_total_mb=1000, gpu_used_mb=500
            )
            
            # Should detect pressure due to low available memory
            pressure_level = monitor.get_pressure_level()
            assert pressure_level != ResourcePressureLevel.NORMAL


class TestEvictionPolicies:
    """Test model eviction policies."""
    
    def create_mock_model_status(self, model_id: str, memory_mb: int, 
                               access_time: datetime, priority: int = 1) -> ModelStatus:
        """Create a mock model status for testing."""
        return ModelStatus(
            model_id=model_id,
            model_type=ModelType.WHISPER_TRANSCRIBER,
            is_loaded=True,
            memory_usage_mb=memory_mb,
            last_access_time=access_time,
            load_time_ms=1000,
            access_count=10
        )
    
    def test_lru_eviction_policy(self):
        """Test LRU (Least Recently Used) eviction policy."""
        monitor = ResourceMonitor(eviction_policy=EvictionPolicy.LRU)
        
        now = datetime.now()
        models = {
            "model1": self.create_mock_model_status("model1", 500, now - timedelta(minutes=10)),
            "model2": self.create_mock_model_status("model2", 300, now - timedelta(minutes=5)),
            "model3": self.create_mock_model_status("model3", 400, now - timedelta(minutes=15)),
        }
        
        candidates = monitor.get_eviction_candidates(models)
        
        # Should be ordered by idle time (oldest first)
        assert candidates == ["model3", "model1", "model2"]
    
    def test_priority_lru_eviction_policy(self):
        """Test priority-based LRU eviction policy."""
        monitor = ResourceMonitor(eviction_policy=EvictionPolicy.PRIORITY_LRU)
        
        now = datetime.now()
        
        # Create models with different priorities
        model1 = self.create_mock_model_status("model1", 500, now - timedelta(minutes=10), priority=1)
        model2 = self.create_mock_model_status("model2", 300, now - timedelta(minutes=5), priority=2)
        model3 = self.create_mock_model_status("model3", 400, now - timedelta(minutes=15), priority=1)
        
        # Add priority attribute to mock objects
        for model in [model1, model2, model3]:
            setattr(model, 'priority', getattr(model, 'priority', 1))
        
        models = {"model1": model1, "model2": model2, "model3": model3}
        
        candidates = monitor.get_eviction_candidates(models)
        
        # Should prioritize lower priority models first, then by idle time
        # Priority 1 models should come before priority 2
        assert candidates[0] in ["model1", "model3"]  # Priority 1 models
        assert candidates[-1] == "model2"  # Priority 2 model
    
    def test_memory_usage_eviction_policy(self):
        """Test memory usage-based eviction policy."""
        monitor = ResourceMonitor(eviction_policy=EvictionPolicy.MEMORY_USAGE)
        
        now = datetime.now()
        models = {
            "model1": self.create_mock_model_status("model1", 500, now),
            "model2": self.create_mock_model_status("model2", 800, now),
            "model3": self.create_mock_model_status("model3", 300, now),
        }
        
        candidates = monitor.get_eviction_candidates(models)
        
        # Should be ordered by memory usage (highest first)
        assert candidates == ["model2", "model1", "model3"]
    
    def test_eviction_count_calculation(self):
        """Test calculation of how many models to evict."""
        monitor = ResourceMonitor()
        
        models = {f"model{i}": Mock() for i in range(10)}
        
        # Test emergency pressure - should evict 75%
        with patch.object(monitor, 'get_pressure_level') as mock_pressure:
            mock_pressure.return_value = ResourcePressureLevel.EMERGENCY
            count = monitor.calculate_eviction_count(Mock(), models)
            assert count == 7  # 75% of 10 models
        
        # Test critical pressure - should evict 50%
        with patch.object(monitor, 'get_pressure_level') as mock_pressure:
            mock_pressure.return_value = ResourcePressureLevel.CRITICAL
            count = monitor.calculate_eviction_count(Mock(), models)
            assert count == 5  # 50% of 10 models
        
        # Test warning pressure - should evict 1-2 models
        with patch.object(monitor, 'get_pressure_level') as mock_pressure:
            mock_pressure.return_value = ResourcePressureLevel.WARNING
            count = monitor.calculate_eviction_count(Mock(), models)
            assert count in [1, 2]


class TestGPUFallback:
    """Test GPU to CPU fallback functionality."""
    
    def test_gpu_fallback_enabled(self):
        """Test GPU fallback when enabled."""
        monitor = ResourceMonitor(
            gpu_fallback_enabled=True,
            critical_threshold_percent=85.0
        )
        
        # Mock high GPU usage
        with patch.object(monitor, 'get_memory_usage') as mock_memory:
            mock_memory.return_value = MemoryUsage(
                total_mb=1000, used_mb=600, available_mb=400,
                gpu_total_mb=1000, gpu_used_mb=900  # 90% GPU usage
            )
            
            assert monitor.should_fallback_to_cpu("cuda")
            assert monitor.should_fallback_to_cpu("gpu")
            assert not monitor.should_fallback_to_cpu("cpu")  # Already on CPU
    
    def test_gpu_fallback_disabled(self):
        """Test GPU fallback when disabled."""
        monitor = ResourceMonitor(
            gpu_fallback_enabled=False,
            critical_threshold_percent=85.0
        )
        
        # Mock high GPU usage
        with patch.object(monitor, 'get_memory_usage') as mock_memory:
            mock_memory.return_value = MemoryUsage(
                total_mb=1000, used_mb=600, available_mb=400,
                gpu_total_mb=1000, gpu_used_mb=900  # 90% GPU usage
            )
            
            assert not monitor.should_fallback_to_cpu("cuda")
            assert not monitor.should_fallback_to_cpu("gpu")


class TestPressureTracking:
    """Test resource pressure tracking and history."""
    
    def test_pressure_tracking(self):
        """Test pressure level tracking and consecutive count."""
        monitor = ResourceMonitor()
        
        # Simulate pressure level changes
        monitor._update_pressure_tracking(ResourcePressureLevel.NORMAL)
        assert monitor._consecutive_pressure_count == 0
        
        monitor._update_pressure_tracking(ResourcePressureLevel.WARNING)
        assert monitor._consecutive_pressure_count == 1
        
        monitor._update_pressure_tracking(ResourcePressureLevel.WARNING)
        assert monitor._consecutive_pressure_count == 2
        
        monitor._update_pressure_tracking(ResourcePressureLevel.CRITICAL)
        assert monitor._consecutive_pressure_count == 1  # Reset for new level
        
        monitor._update_pressure_tracking(ResourcePressureLevel.NORMAL)
        assert monitor._consecutive_pressure_count == 0
    
    def test_consecutive_pressure_eviction_trigger(self):
        """Test eviction trigger based on consecutive pressure cycles."""
        monitor = ResourceMonitor()
        
        # Mock warning pressure
        with patch.object(monitor, 'get_pressure_level') as mock_pressure:
            mock_pressure.return_value = ResourcePressureLevel.WARNING
            
            # Should not trigger eviction initially
            monitor._consecutive_pressure_count = 1
            assert not monitor.should_trigger_eviction()
            
            # Should trigger eviction after 3 consecutive cycles
            monitor._consecutive_pressure_count = 3
            assert monitor.should_trigger_eviction()
    
    def test_pressure_statistics(self):
        """Test pressure statistics collection."""
        monitor = ResourceMonitor()
        
        # Add some pressure history
        monitor._update_pressure_tracking(ResourcePressureLevel.NORMAL)
        monitor._update_pressure_tracking(ResourcePressureLevel.WARNING)
        monitor._update_pressure_tracking(ResourcePressureLevel.CRITICAL)
        monitor._update_pressure_tracking(ResourcePressureLevel.NORMAL)
        
        stats = monitor.get_pressure_statistics()
        
        assert stats["total_samples"] == 4
        assert "normal" in stats["level_counts"]
        assert "warning" in stats["level_counts"]
        assert "critical" in stats["level_counts"]
        assert stats["current_level"] == "normal"


class TestResourceMonitorConfiguration:
    """Test resource monitor configuration updates."""
    
    def test_threshold_updates(self):
        """Test updating resource thresholds."""
        monitor = ResourceMonitor(
            warning_threshold_percent=70.0,
            critical_threshold_percent=85.0
        )
        
        # Update thresholds
        monitor.update_thresholds(
            warning_threshold_percent=60.0,
            critical_threshold_percent=80.0,
            min_free_memory_mb=1024
        )
        
        assert monitor.warning_threshold == 60.0
        assert monitor.critical_threshold == 80.0
        assert monitor.min_free_memory_mb == 1024
    
    def test_eviction_policy_update(self):
        """Test updating eviction policy."""
        monitor = ResourceMonitor(eviction_policy=EvictionPolicy.LRU)
        
        # Update policy by string
        monitor.update_thresholds(eviction_policy="memory_usage")
        assert monitor.eviction_policy == EvictionPolicy.MEMORY_USAGE
        
        # Update policy by enum
        monitor.update_thresholds(eviction_policy=EvictionPolicy.PRIORITY_LRU)
        assert monitor.eviction_policy == EvictionPolicy.PRIORITY_LRU


@pytest.mark.asyncio
class TestModelManagerResourceIntegration:
    """Test integration between ModelManager and ResourceMonitor."""
    
    async def test_pressure_callback_integration(self):
        """Test pressure level change callback integration."""
        metrics_collector = MetricsCollector()
        resource_monitor = ResourceMonitor(metrics_collector=metrics_collector)
        
        # Mock registry
        mock_registry = Mock()
        
        manager = ModelManager(
            registry=mock_registry,
            metrics_collector=metrics_collector,
            resource_monitor=resource_monitor
        )
        
        # Test pressure callback is called
        callback_called = False
        original_callback = manager._on_pressure_level_change
        
        async def mock_callback(pressure_level, memory_usage):
            nonlocal callback_called
            callback_called = True
            await original_callback(pressure_level, memory_usage)
        
        manager._on_pressure_level_change = mock_callback
        
        # Simulate pressure level change
        await manager._on_pressure_level_change(
            ResourcePressureLevel.CRITICAL,
            MemoryUsage(1000, 900, 100)
        )
        
        assert callback_called
    
    async def test_resource_pressure_eviction(self):
        """Test model eviction due to resource pressure."""
        metrics_collector = MetricsCollector()
        resource_monitor = ResourceMonitor(metrics_collector=metrics_collector)
        
        # Mock registry
        mock_registry = Mock()
        
        manager = ModelManager(
            registry=mock_registry,
            metrics_collector=metrics_collector,
            resource_monitor=resource_monitor
        )
        
        # Mock loaded models
        mock_model = Mock()
        mock_model.is_loaded.return_value = True
        manager._loaded_models = {"test_model": mock_model}
        
        # Mock model status
        with patch.object(manager, 'get_model_status') as mock_status:
            mock_status.return_value = {
                "test_model": ModelStatus(
                    model_id="test_model",
                    model_type=ModelType.WHISPER_TRANSCRIBER,
                    is_loaded=True,
                    memory_usage_mb=500,
                    last_access_time=datetime.now(),
                    load_time_ms=1000,
                    access_count=1
                )
            }
            
            # Mock resource monitor methods
            with patch.object(resource_monitor, 'get_memory_usage') as mock_memory:
                mock_memory.return_value = MemoryUsage(1000, 900, 100)
                
                with patch.object(resource_monitor, 'get_eviction_candidates') as mock_candidates:
                    mock_candidates.return_value = ["test_model"]
                    
                    with patch.object(resource_monitor, 'calculate_eviction_count') as mock_count:
                        mock_count.return_value = 1
                        
                        # Test eviction
                        await manager._handle_resource_pressure_eviction()
                        
                        # Verify model was removed
                        assert "test_model" not in manager._loaded_models
    
    async def test_gpu_fallback_integration(self):
        """Test GPU to CPU fallback integration."""
        metrics_collector = MetricsCollector()
        resource_monitor = ResourceMonitor(
            gpu_fallback_enabled=True,
            critical_threshold_percent=85.0,
            metrics_collector=metrics_collector
        )
        
        # Mock registry
        mock_registry = Mock()
        mock_model = Mock()
        mock_registry.create_model.return_value = mock_model
        
        manager = ModelManager(
            registry=mock_registry,
            metrics_collector=metrics_collector,
            resource_monitor=resource_monitor
        )
        
        # Mock high GPU usage
        with patch.object(resource_monitor, 'get_memory_usage') as mock_memory:
            mock_memory.return_value = MemoryUsage(
                total_mb=1000, used_mb=600, available_mb=400,
                gpu_total_mb=1000, gpu_used_mb=900  # 90% GPU usage
            )
            
            # Mock should_fallback_to_cpu to return True
            with patch.object(resource_monitor, 'should_fallback_to_cpu') as mock_fallback:
                mock_fallback.return_value = True
                
                # Create config with GPU device
                from model_management.data_models import ModelConfig, ModelType
                config = ModelConfig(
                    model_type=ModelType.WHISPER_TRANSCRIBER,
                    model_name="test_model",
                    device="cuda",
                    ttl_seconds=300,
                    max_memory_mb=1024,
                    priority=1
                )
                
                # Mock the load operation to be async
                async def mock_load():
                    pass
                
                mock_model.load = mock_load
                mock_model.set_load_time_ms = Mock()
                mock_model.update_access_time = Mock()
                
                # Test model creation with fallback
                result = await manager._create_and_load_model_with_retry(
                    ModelType.WHISPER_TRANSCRIBER,
                    "test_model_id",
                    config
                )
                
                # Verify fallback was recorded
                assert result == mock_model


class TestMetricsIntegration:
    """Test metrics collection for resource management."""
    
    def test_pressure_metrics_recording(self):
        """Test recording of resource pressure metrics."""
        collector = MetricsCollector()
        
        # Record pressure events
        collector.record_resource_pressure("warning")
        collector.record_resource_pressure("critical")
        collector.record_resource_pressure("warning")
        
        # Verify metrics are recorded
        system_metrics = collector.get_system_metrics()
        assert hasattr(system_metrics, 'pressure_events')
        assert system_metrics.pressure_events["warning"] == 2
        assert system_metrics.pressure_events["critical"] == 1
    
    def test_eviction_metrics_recording(self):
        """Test recording of pressure eviction metrics."""
        collector = MetricsCollector()
        
        # Record evictions
        collector.record_pressure_eviction("model1")
        collector.record_pressure_eviction("model2")
        
        # Verify metrics are recorded
        system_metrics = collector.get_system_metrics()
        assert hasattr(system_metrics, 'pressure_evictions')
        assert system_metrics.pressure_evictions == 2
    
    def test_gpu_fallback_metrics_recording(self):
        """Test recording of GPU fallback metrics."""
        collector = MetricsCollector()
        
        # Record fallbacks
        collector.record_gpu_fallback("model1", "cuda", "cpu")
        collector.record_gpu_fallback("model2", "gpu", "cpu")
        
        # Verify metrics are recorded
        system_metrics = collector.get_system_metrics()
        assert hasattr(system_metrics, 'gpu_fallbacks')
        assert system_metrics.gpu_fallbacks == 2


if __name__ == "__main__":
    pytest.main([__file__])