"""
Integration tests for metrics collection with model management.
"""

import pytest
from unittest.mock import Mock, AsyncMock
from datetime import datetime

from server.model_management.model_manager import ModelManager
from server.model_management.model_registry import ModelRegistry
from server.model_management.metrics_collector import MetricsCollector
from server.model_management.resource_monitor import ResourceMonitor
from server.model_management.data_models import ModelType, ModelConfig, MemoryUsage


class TestMetricsIntegration:
    """Integration tests for metrics collection with model management components."""
    
    def setup_method(self):
        """Setup integration test fixtures."""
        self.metrics_collector = MetricsCollector()
        self.mock_registry = Mock(spec=ModelRegistry)
        self.model_manager = ModelManager(self.mock_registry, self.metrics_collector)
        self.resource_monitor = ResourceMonitor(metrics_collector=self.metrics_collector)
    
    def test_model_manager_metrics_integration(self):
        """Test that ModelManager properly integrates with MetricsCollector."""
        # Verify metrics collector is set
        assert self.model_manager._metrics_collector == self.metrics_collector
        
        # Verify we can get metrics collector
        collector = self.model_manager.get_metrics_collector()
        assert collector == self.metrics_collector
        
        # Verify performance metrics method exists
        performance_metrics = self.model_manager.get_performance_metrics()
        assert isinstance(performance_metrics, dict)
        assert "uptime_seconds" in performance_metrics
        assert "system_metrics" in performance_metrics
    
    def test_resource_monitor_metrics_integration(self):
        """Test that ResourceMonitor properly integrates with MetricsCollector."""
        # Verify metrics collector is set
        assert self.resource_monitor._metrics_collector == self.metrics_collector
        
        # Test memory usage recording
        memory_usage = MemoryUsage(
            total_mb=8192, used_mb=4096, available_mb=4096
        )
        
        # Simulate what happens in the monitoring loop
        self.resource_monitor._metrics_collector.record_system_memory_usage(memory_usage)
        
        # Verify it was recorded
        system_metrics = self.metrics_collector.get_system_metrics()
        assert len(system_metrics.memory_usage_history) == 1
        assert system_metrics.memory_usage_history[0] == memory_usage
    
    def test_metrics_export_integration(self):
        """Test that metrics can be properly exported."""
        # Record some test data
        model_id = "test_model"
        self.metrics_collector.record_model_load_start(
            model_id, ModelType.WHISPER_TRANSCRIBER, "whisper-base", "cpu"
        )
        self.metrics_collector.record_model_load_success(model_id, 1500)
        self.metrics_collector.record_cache_hit(model_id)
        
        # Record system memory usage
        memory_usage = MemoryUsage(
            total_mb=16384, used_mb=8192, available_mb=8192
        )
        self.metrics_collector.record_system_memory_usage(memory_usage)
        
        # Export metrics
        exported = self.metrics_collector.export_metrics()
        
        # Verify structure
        assert "collection_start_time" in exported
        assert "uptime_seconds" in exported
        assert "system_metrics" in exported
        assert "model_metrics" in exported
        
        # Verify system metrics
        system_data = exported["system_metrics"]
        assert system_data["total_models_created"] == 1
        assert system_data["active_models_count"] == 1
        assert system_data["cache_hits"] == 1
        assert system_data["cache_hit_rate"] == 100.0  # Only hits, no misses
        
        # Verify model metrics
        model_data = exported["model_metrics"][model_id]
        assert model_data["model_type"] == "whisper_transcriber"
        assert model_data["model_name"] == "whisper-base"
        assert model_data["device"] == "cpu"
        assert model_data["total_loads"] == 1
        assert model_data["is_currently_loaded"]
        assert model_data["load_success_rate"] == 100.0
    
    def test_error_handling_metrics_integration(self):
        """Test that error handling properly integrates with metrics."""
        # Record some errors
        model_id = "error_test_model"
        
        # Record load failure
        error = Exception("Test load error")
        self.metrics_collector.record_model_load_failure(model_id, error)
        
        # Record unload failure
        unload_error = RuntimeError("Test unload error")
        self.metrics_collector.record_model_unload_failure(model_id, unload_error)
        
        # Check system metrics
        system_metrics = self.metrics_collector.get_system_metrics()
        assert system_metrics.total_errors == 2
        assert "Exception" in system_metrics.error_types
        assert "RuntimeError" in system_metrics.error_types
        assert system_metrics.error_types["Exception"] == 1
        assert system_metrics.error_types["RuntimeError"] == 1
        
        # Check performance summary
        performance = self.metrics_collector.get_performance_summary()
        assert performance["system_metrics"]["total_errors"] == 2
        assert "Exception" in performance["error_breakdown"]
        assert "RuntimeError" in performance["error_breakdown"]
    
    def test_cleanup_metrics_integration(self):
        """Test that cleanup operations are properly tracked in metrics."""
        # Record cleanup operation
        models_cleaned = 3
        self.metrics_collector.record_cleanup_operation(models_cleaned)
        
        # Verify metrics
        system_metrics = self.metrics_collector.get_system_metrics()
        assert system_metrics.cleanup_runs == 1
        assert system_metrics.models_cleaned_up == 3
        
        # Record another cleanup
        self.metrics_collector.record_cleanup_operation(2)
        
        # Verify cumulative metrics
        updated_metrics = self.metrics_collector.get_system_metrics()
        assert updated_metrics.cleanup_runs == 2
        assert updated_metrics.models_cleaned_up == 5
    
    def test_memory_tracking_integration(self):
        """Test memory usage tracking integration."""
        model_id = "memory_test_model"
        
        # Setup model
        self.metrics_collector.record_model_load_start(
            model_id, ModelType.WHISPER_TRANSCRIBER, "whisper-base", "cpu"
        )
        
        # Record memory usage progression
        memory_usages = [
            MemoryUsage(total_mb=8192, used_mb=1024, available_mb=7168),
            MemoryUsage(total_mb=8192, used_mb=2048, available_mb=6144),
            MemoryUsage(total_mb=8192, used_mb=3072, available_mb=5120),
            MemoryUsage(total_mb=8192, used_mb=2048, available_mb=6144),  # Decreased
        ]
        
        for memory_usage in memory_usages:
            self.metrics_collector.record_memory_usage(model_id, memory_usage)
        
        # Verify model metrics
        model_metrics = self.metrics_collector.get_model_metrics(model_id)
        assert model_metrics.peak_memory_usage == 3072  # Highest usage
        assert model_metrics.current_memory_usage == 2048  # Last usage
        assert len(model_metrics.memory_usage_history) == 4
        
        # Record system memory usage
        system_memory = MemoryUsage(
            total_mb=16384, used_mb=8192, available_mb=8192,
            gpu_total_mb=8192, gpu_used_mb=4096
        )
        self.metrics_collector.record_system_memory_usage(system_memory)
        
        # Verify system metrics
        system_metrics = self.metrics_collector.get_system_metrics()
        assert len(system_metrics.memory_usage_history) == 1
        assert system_metrics.average_memory_usage == 50.0  # 8192/16384 * 100