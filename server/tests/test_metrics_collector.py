"""
Unit tests for the MetricsCollector class.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch
import time

from server.model_management.metrics_collector import (
    MetricsCollector, ModelMetrics, SystemMetrics
)
from server.model_management.data_models import ModelType, MemoryUsage


class TestModelMetrics:
    """Test cases for ModelMetrics data class."""
    
    def test_model_metrics_initialization(self):
        """Test ModelMetrics initialization with default values."""
        metrics = ModelMetrics(
            model_id="test_model",
            model_type=ModelType.WHISPER_TRANSCRIBER,
            model_name="whisper-base",
            device="cpu"
        )
        
        assert metrics.model_id == "test_model"
        assert metrics.model_type == ModelType.WHISPER_TRANSCRIBER
        assert metrics.model_name == "whisper-base"
        assert metrics.device == "cpu"
        assert metrics.total_loads == 0
        assert metrics.total_unloads == 0
        assert metrics.failed_loads == 0
        assert metrics.failed_unloads == 0
        assert len(metrics.load_times) == 0
        assert len(metrics.unload_times) == 0
        assert metrics.total_accesses == 0
        assert metrics.last_access_time is None
        assert metrics.peak_memory_usage == 0
        assert metrics.current_memory_usage == 0
        assert not metrics.is_currently_loaded
        assert metrics.load_start_time is None
        assert metrics.total_loaded_time_seconds == 0.0
    
    def test_average_load_time_empty(self):
        """Test average load time calculation with no data."""
        metrics = ModelMetrics(
            model_id="test",
            model_type=ModelType.WHISPER_TRANSCRIBER,
            model_name="test",
            device="cpu"
        )
        
        assert metrics.average_load_time == 0.0
    
    def test_average_load_time_with_data(self):
        """Test average load time calculation with data."""
        metrics = ModelMetrics(
            model_id="test",
            model_type=ModelType.WHISPER_TRANSCRIBER,
            model_name="test",
            device="cpu"
        )
        
        metrics.load_times.extend([100, 200, 300])
        assert metrics.average_load_time == 200.0
    
    def test_load_success_rate_no_attempts(self):
        """Test load success rate with no attempts."""
        metrics = ModelMetrics(
            model_id="test",
            model_type=ModelType.WHISPER_TRANSCRIBER,
            model_name="test",
            device="cpu"
        )
        
        assert metrics.load_success_rate == 100.0
    
    def test_load_success_rate_with_failures(self):
        """Test load success rate with some failures."""
        metrics = ModelMetrics(
            model_id="test",
            model_type=ModelType.WHISPER_TRANSCRIBER,
            model_name="test",
            device="cpu"
        )
        
        metrics.total_loads = 8
        metrics.failed_loads = 2
        assert metrics.load_success_rate == 80.0


class TestSystemMetrics:
    """Test cases for SystemMetrics data class."""
    
    def test_system_metrics_initialization(self):
        """Test SystemMetrics initialization with default values."""
        metrics = SystemMetrics()
        
        assert metrics.total_models_created == 0
        assert metrics.total_models_destroyed == 0
        assert metrics.active_models_count == 0
        assert len(metrics.memory_usage_history) == 0
        assert metrics.cache_hits == 0
        assert metrics.cache_misses == 0
        assert metrics.total_errors == 0
        assert len(metrics.error_types) == 0
        assert metrics.cleanup_runs == 0
        assert metrics.models_cleaned_up == 0
    
    def test_cache_hit_rate_no_requests(self):
        """Test cache hit rate with no requests."""
        metrics = SystemMetrics()
        assert metrics.cache_hit_rate == 0.0
    
    def test_cache_hit_rate_with_data(self):
        """Test cache hit rate calculation."""
        metrics = SystemMetrics()
        metrics.cache_hits = 80
        metrics.cache_misses = 20
        assert metrics.cache_hit_rate == 80.0
    
    def test_average_memory_usage_empty(self):
        """Test average memory usage with no data."""
        metrics = SystemMetrics()
        assert metrics.average_memory_usage == 0.0
    
    def test_average_memory_usage_with_data(self):
        """Test average memory usage calculation."""
        metrics = SystemMetrics()
        
        # Add some memory usage data
        usage1 = MemoryUsage(total_mb=1000, used_mb=500, available_mb=500)  # 50%
        usage2 = MemoryUsage(total_mb=1000, used_mb=600, available_mb=400)  # 60%
        usage3 = MemoryUsage(total_mb=1000, used_mb=700, available_mb=300)  # 70%
        
        metrics.memory_usage_history.extend([usage1, usage2, usage3])
        assert metrics.average_memory_usage == 60.0


class TestMetricsCollector:
    """Test cases for MetricsCollector class."""
    
    def setup_method(self):
        """Setup test fixtures."""
        self.collector = MetricsCollector()
        self.model_id = "test_model_123"
        self.model_type = ModelType.WHISPER_TRANSCRIBER
        self.model_name = "whisper-base"
        self.device = "cpu"
    
    def test_initialization(self):
        """Test MetricsCollector initialization."""
        collector = MetricsCollector()
        
        assert collector._monitoring_enabled
        assert collector._detailed_logging
        assert len(collector._model_metrics) == 0
        assert isinstance(collector._system_metrics, SystemMetrics)
        assert isinstance(collector._collection_start_time, datetime)
    
    def test_record_model_load_start(self):
        """Test recording model load start."""
        self.collector.record_model_load_start(
            self.model_id, self.model_type, self.model_name, self.device
        )
        
        assert self.model_id in self.collector._model_metrics
        metrics = self.collector._model_metrics[self.model_id]
        assert metrics.model_id == self.model_id
        assert metrics.model_type == self.model_type
        assert metrics.model_name == self.model_name
        assert metrics.device == self.device
        assert metrics.load_start_time is not None
    
    def test_record_model_load_success(self):
        """Test recording successful model load."""
        # First record load start
        self.collector.record_model_load_start(
            self.model_id, self.model_type, self.model_name, self.device
        )
        
        # Then record success
        load_time_ms = 1500
        self.collector.record_model_load_success(self.model_id, load_time_ms)
        
        metrics = self.collector._model_metrics[self.model_id]
        assert metrics.total_loads == 1
        assert load_time_ms in metrics.load_times
        assert metrics.is_currently_loaded
        assert metrics.load_start_time is not None
        
        # Check system metrics
        assert self.collector._system_metrics.total_models_created == 1
        assert self.collector._system_metrics.active_models_count == 1
    
    def test_record_model_load_failure(self):
        """Test recording failed model load."""
        error = Exception("Load failed")
        self.collector.record_model_load_failure(self.model_id, error)
        
        # Should create metrics entry if it doesn't exist
        if self.model_id in self.collector._model_metrics:
            metrics = self.collector._model_metrics[self.model_id]
            assert metrics.failed_loads == 1
        
        # Check system error metrics
        assert self.collector._system_metrics.total_errors == 1
        assert "Exception" in self.collector._system_metrics.error_types
        assert self.collector._system_metrics.error_types["Exception"] == 1
    
    def test_record_model_unload_success(self):
        """Test recording successful model unload."""
        # Setup loaded model
        self.collector.record_model_load_start(
            self.model_id, self.model_type, self.model_name, self.device
        )
        self.collector.record_model_load_success(self.model_id, 1000)
        
        # Record unload
        unload_time_ms = 500
        self.collector.record_model_unload_success(self.model_id, unload_time_ms)
        
        metrics = self.collector._model_metrics[self.model_id]
        assert metrics.total_unloads == 1
        assert unload_time_ms in metrics.unload_times
        assert not metrics.is_currently_loaded
        assert metrics.load_start_time is None
        
        # Check system metrics
        assert self.collector._system_metrics.active_models_count == 0
    
    def test_record_model_access(self):
        """Test recording model access."""
        # Setup model
        self.collector.record_model_load_start(
            self.model_id, self.model_type, self.model_name, self.device
        )
        
        # Record access
        self.collector.record_model_access(self.model_id)
        
        metrics = self.collector._model_metrics[self.model_id]
        assert metrics.total_accesses == 1
        assert metrics.last_access_time is not None
    
    def test_record_memory_usage(self):
        """Test recording memory usage for a model."""
        # Setup model
        self.collector.record_model_load_start(
            self.model_id, self.model_type, self.model_name, self.device
        )
        
        # Record memory usage
        memory_usage = MemoryUsage(
            total_mb=8192, used_mb=2048, available_mb=6144
        )
        self.collector.record_memory_usage(self.model_id, memory_usage)
        
        metrics = self.collector._model_metrics[self.model_id]
        assert metrics.current_memory_usage == 2048
        assert 2048 in metrics.memory_usage_history
        assert metrics.peak_memory_usage == 2048
        
        # Record higher usage
        higher_usage = MemoryUsage(
            total_mb=8192, used_mb=4096, available_mb=4096
        )
        self.collector.record_memory_usage(self.model_id, higher_usage)
        
        assert metrics.peak_memory_usage == 4096
    
    def test_record_system_memory_usage(self):
        """Test recording system-wide memory usage."""
        memory_usage = MemoryUsage(
            total_mb=16384, used_mb=8192, available_mb=8192
        )
        
        self.collector.record_system_memory_usage(memory_usage)
        
        assert memory_usage in self.collector._system_metrics.memory_usage_history
    
    def test_record_cache_hit(self):
        """Test recording cache hit."""
        # Setup model
        self.collector.record_model_load_start(
            self.model_id, self.model_type, self.model_name, self.device
        )
        
        self.collector.record_cache_hit(self.model_id)
        
        assert self.collector._system_metrics.cache_hits == 1
        # Should also record model access
        metrics = self.collector._model_metrics[self.model_id]
        assert metrics.total_accesses == 1
    
    def test_record_cache_miss(self):
        """Test recording cache miss."""
        self.collector.record_cache_miss(self.model_id)
        
        assert self.collector._system_metrics.cache_misses == 1
    
    def test_record_cleanup_operation(self):
        """Test recording cleanup operation."""
        models_cleaned = 3
        self.collector.record_cleanup_operation(models_cleaned)
        
        assert self.collector._system_metrics.cleanup_runs == 1
        assert self.collector._system_metrics.models_cleaned_up == 3
    
    def test_get_model_metrics(self):
        """Test getting metrics for a specific model."""
        # Setup model
        self.collector.record_model_load_start(
            self.model_id, self.model_type, self.model_name, self.device
        )
        
        metrics = self.collector.get_model_metrics(self.model_id)
        assert metrics is not None
        assert metrics.model_id == self.model_id
        
        # Test non-existent model
        assert self.collector.get_model_metrics("non_existent") is None
    
    def test_get_all_model_metrics(self):
        """Test getting all model metrics."""
        # Setup multiple models
        model_ids = ["model1", "model2", "model3"]
        for model_id in model_ids:
            self.collector.record_model_load_start(
                model_id, self.model_type, f"model_{model_id}", self.device
            )
        
        all_metrics = self.collector.get_all_model_metrics()
        assert len(all_metrics) == 3
        for model_id in model_ids:
            assert model_id in all_metrics
    
    def test_get_system_metrics(self):
        """Test getting system metrics."""
        system_metrics = self.collector.get_system_metrics()
        assert isinstance(system_metrics, SystemMetrics)
    
    def test_get_performance_summary(self):
        """Test getting performance summary."""
        # Setup some data
        self.collector.record_model_load_start(
            self.model_id, self.model_type, self.model_name, self.device
        )
        self.collector.record_model_load_success(self.model_id, 1000)
        self.collector.record_cache_hit(self.model_id)
        self.collector.record_cache_miss("other_model")
        
        summary = self.collector.get_performance_summary()
        
        assert "uptime_seconds" in summary
        assert "system_metrics" in summary
        assert "performance_metrics" in summary
        assert "error_breakdown" in summary
        
        system_metrics = summary["system_metrics"]
        assert system_metrics["active_models"] == 1
        assert system_metrics["total_models_created"] == 1
        assert system_metrics["cache_hit_rate"] == 50.0  # 1 hit, 1 miss
    
    def test_reset_metrics(self):
        """Test resetting all metrics."""
        # Setup some data
        self.collector.record_model_load_start(
            self.model_id, self.model_type, self.model_name, self.device
        )
        self.collector.record_model_load_success(self.model_id, 1000)
        
        # Verify data exists
        assert len(self.collector._model_metrics) > 0
        assert self.collector._system_metrics.total_models_created > 0
        
        # Reset
        self.collector.reset_metrics()
        
        # Verify reset
        assert len(self.collector._model_metrics) == 0
        assert self.collector._system_metrics.total_models_created == 0
    
    def test_enable_disable_monitoring(self):
        """Test enabling and disabling monitoring."""
        # Disable monitoring
        self.collector.disable_monitoring()
        assert not self.collector._monitoring_enabled
        
        # Try to record data - should be ignored
        self.collector.record_model_load_start(
            self.model_id, self.model_type, self.model_name, self.device
        )
        assert len(self.collector._model_metrics) == 0
        
        # Re-enable monitoring
        self.collector.enable_monitoring()
        assert self.collector._monitoring_enabled
        
        # Now recording should work
        self.collector.record_model_load_start(
            self.model_id, self.model_type, self.model_name, self.device
        )
        assert len(self.collector._model_metrics) == 1
    
    def test_export_metrics(self):
        """Test exporting all metrics."""
        # Setup some data
        self.collector.record_model_load_start(
            self.model_id, self.model_type, self.model_name, self.device
        )
        self.collector.record_model_load_success(self.model_id, 1500)
        self.collector.record_model_access(self.model_id)
        
        exported = self.collector.export_metrics()
        
        assert "collection_start_time" in exported
        assert "uptime_seconds" in exported
        assert "system_metrics" in exported
        assert "model_metrics" in exported
        
        # Check model metrics structure
        model_metrics = exported["model_metrics"]
        assert self.model_id in model_metrics
        
        model_data = model_metrics[self.model_id]
        assert model_data["model_type"] == self.model_type.value
        assert model_data["model_name"] == self.model_name
        assert model_data["device"] == self.device
        assert model_data["total_loads"] == 1
        assert model_data["total_accesses"] == 1
        assert model_data["is_currently_loaded"]
    
    def test_thread_safety(self):
        """Test basic thread safety of metrics collection."""
        import threading
        import time
        
        def record_operations():
            for i in range(10):
                model_id = f"model_{threading.current_thread().ident}_{i}"
                self.collector.record_model_load_start(
                    model_id, self.model_type, f"model_{i}", self.device
                )
                self.collector.record_model_load_success(model_id, 1000 + i)
                self.collector.record_model_access(model_id)
                time.sleep(0.001)  # Small delay to simulate real work
        
        # Create multiple threads
        threads = []
        for _ in range(3):
            thread = threading.Thread(target=record_operations)
            threads.append(thread)
        
        # Start all threads
        for thread in threads:
            thread.start()
        
        # Wait for completion
        for thread in threads:
            thread.join()
        
        # Verify all operations were recorded
        all_metrics = self.collector.get_all_model_metrics()
        assert len(all_metrics) == 30  # 3 threads * 10 models each
        
        system_metrics = self.collector.get_system_metrics()
        assert system_metrics.total_models_created == 30
        assert system_metrics.active_models_count == 30


class TestMetricsCollectorIntegration:
    """Integration tests for MetricsCollector with other components."""
    
    def test_memory_usage_tracking_integration(self):
        """Test integration with memory usage tracking."""
        collector = MetricsCollector()
        model_id = "integration_test_model"
        
        # Simulate model lifecycle with memory tracking
        collector.record_model_load_start(
            model_id, ModelType.WHISPER_TRANSCRIBER, "whisper-base", "cpu"
        )
        
        # Record initial memory usage
        initial_memory = MemoryUsage(
            total_mb=8192, used_mb=1024, available_mb=7168
        )
        collector.record_memory_usage(model_id, initial_memory)
        
        # Record successful load
        collector.record_model_load_success(model_id, 2000)
        
        # Record peak memory usage during operation
        peak_memory = MemoryUsage(
            total_mb=8192, used_mb=3072, available_mb=5120
        )
        collector.record_memory_usage(model_id, peak_memory)
        
        # Record multiple accesses
        for _ in range(5):
            collector.record_model_access(model_id)
        
        # Record system memory usage
        system_memory = MemoryUsage(
            total_mb=16384, used_mb=8192, available_mb=8192,
            gpu_total_mb=8192, gpu_used_mb=2048
        )
        collector.record_system_memory_usage(system_memory)
        
        # Verify metrics
        model_metrics = collector.get_model_metrics(model_id)
        assert model_metrics.peak_memory_usage == 3072
        assert model_metrics.current_memory_usage == 3072
        assert model_metrics.total_accesses == 5
        assert model_metrics.is_currently_loaded
        
        system_metrics = collector.get_system_metrics()
        assert len(system_metrics.memory_usage_history) == 1
        assert system_metrics.memory_usage_history[0] == system_memory
        
        # Test unload
        collector.record_model_unload_start(model_id)
        collector.record_model_unload_success(model_id, 500)
        
        assert not model_metrics.is_currently_loaded
        assert model_metrics.total_unloads == 1