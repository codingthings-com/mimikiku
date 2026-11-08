"""
Unit tests for ResourceMonitor class.
"""

import pytest
import time
import threading
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime

from model_management.resource_monitor import ResourceMonitor
from model_management.data_models import MemoryUsage


class TestResourceMonitor:
    """Test cases for ResourceMonitor class."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.monitor = ResourceMonitor(
            memory_threshold_percent=80.0,
            gpu_threshold_percent=85.0,
            monitoring_interval=0.1  # Fast interval for testing
        )
    
    def teardown_method(self):
        """Clean up after tests."""
        if self.monitor._monitoring:
            self.monitor.stop_monitoring()
    
    @patch('model_management.resource_monitor.psutil.virtual_memory')
    def test_get_memory_usage_basic(self, mock_memory):
        """Test basic memory usage retrieval."""
        # Mock psutil memory info
        mock_memory.return_value = Mock(
            total=8 * 1024 * 1024 * 1024,  # 8GB
            used=4 * 1024 * 1024 * 1024,   # 4GB
            available=4 * 1024 * 1024 * 1024  # 4GB
        )
        
        memory_usage = self.monitor.get_memory_usage()
        
        assert memory_usage.total_mb == 8192
        assert memory_usage.used_mb == 4096
        assert memory_usage.available_mb == 4096
        assert memory_usage.usage_percentage == 50.0
    
    @patch('model_management.resource_monitor.GPU_AVAILABLE', True)
    @patch('model_management.resource_monitor.pynvml', create=True)
    @patch('model_management.resource_monitor.psutil.virtual_memory')
    def test_get_memory_usage_with_gpu(self, mock_memory, mock_nvml):
        """Test memory usage retrieval with GPU information."""
        # Mock psutil memory info
        mock_memory.return_value = Mock(
            total=8 * 1024 * 1024 * 1024,
            used=4 * 1024 * 1024 * 1024,
            available=4 * 1024 * 1024 * 1024
        )
        
        # Mock GPU info
        mock_nvml.nvmlDeviceGetMemoryInfo.return_value = Mock(
            total=8192 * 1024 * 1024,  # 8GB GPU
            used=2048 * 1024 * 1024,   # 2GB used
        )
        
        memory_usage = self.monitor.get_memory_usage()
        
        assert memory_usage.gpu_total_mb == 8192
        assert memory_usage.gpu_used_mb == 2048
        assert memory_usage.gpu_usage_percentage == 25.0
    
    @patch('model_management.resource_monitor.GPU_AVAILABLE', False)
    @patch('model_management.resource_monitor.psutil.virtual_memory')
    def test_get_memory_usage_no_gpu(self, mock_memory):
        """Test memory usage retrieval without GPU."""
        mock_memory.return_value = Mock(
            total=8 * 1024 * 1024 * 1024,
            used=4 * 1024 * 1024 * 1024,
            available=4 * 1024 * 1024 * 1024
        )
        
        memory_usage = self.monitor.get_memory_usage()
        
        assert memory_usage.gpu_total_mb == 0
        assert memory_usage.gpu_used_mb == 0
        assert memory_usage.gpu_usage_percentage == 0.0
    
    def test_is_memory_pressure_below_threshold(self):
        """Test memory pressure detection below threshold."""
        with patch.object(self.monitor, 'get_memory_usage') as mock_get_usage:
            mock_get_usage.return_value = MemoryUsage(
                total_mb=8192,
                used_mb=4096,  # 50% usage
                available_mb=4096,
                gpu_total_mb=8192,
                gpu_used_mb=2048  # 25% usage
            )
            
            assert not self.monitor.is_memory_pressure()
    
    def test_is_memory_pressure_above_threshold(self):
        """Test memory pressure detection above threshold."""
        with patch.object(self.monitor, 'get_memory_usage') as mock_get_usage:
            mock_get_usage.return_value = MemoryUsage(
                total_mb=8192,
                used_mb=7000,  # ~85% usage
                available_mb=1192,
                gpu_total_mb=8192,
                gpu_used_mb=2048
            )
            
            assert self.monitor.is_memory_pressure()
    
    @patch('model_management.resource_monitor.GPU_AVAILABLE', True)
    def test_is_memory_pressure_gpu_above_threshold(self):
        """Test memory pressure detection with GPU above threshold."""
        with patch.object(self.monitor, 'get_memory_usage') as mock_get_usage:
            mock_get_usage.return_value = MemoryUsage(
                total_mb=8192,
                used_mb=4096,  # 50% system usage
                available_mb=4096,
                gpu_total_mb=8192,
                gpu_used_mb=7500  # ~91% GPU usage
            )
            
            assert self.monitor.is_memory_pressure()
    
    def test_start_stop_monitoring(self):
        """Test starting and stopping monitoring."""
        callback = Mock()
        
        # Start monitoring
        self.monitor.start_monitoring(callback)
        assert self.monitor._monitoring
        assert self.monitor._monitor_thread is not None
        assert self.monitor._callback == callback
        
        # Give it a moment to start
        time.sleep(0.2)
        
        # Stop monitoring
        self.monitor.stop_monitoring()
        assert not self.monitor._monitoring
        assert self.monitor._callback is None
    
    def test_monitoring_callback_called(self):
        """Test that monitoring callback is called during monitoring."""
        callback = Mock()
        
        with patch.object(self.monitor, 'get_memory_usage') as mock_get_usage:
            mock_get_usage.return_value = MemoryUsage(
                total_mb=8192,
                used_mb=4096,
                available_mb=4096,
                gpu_total_mb=0,
                gpu_used_mb=0
            )
            
            self.monitor.start_monitoring(callback)
            
            # Wait for at least one monitoring cycle
            time.sleep(0.2)
            
            self.monitor.stop_monitoring()
            
            # Callback should have been called at least once
            assert callback.call_count >= 1
            
            # Check that callback was called with MemoryUsage object
            call_args = callback.call_args_list[0][0]
            assert isinstance(call_args[0], MemoryUsage)
    
    def test_monitoring_callback_error_handling(self):
        """Test that monitoring continues even if callback raises exception."""
        callback = Mock(side_effect=Exception("Callback error"))
        
        with patch.object(self.monitor, 'get_memory_usage') as mock_get_usage:
            mock_get_usage.return_value = MemoryUsage(
                total_mb=8192,
                used_mb=4096,
                available_mb=4096,
                gpu_total_mb=0,
                gpu_used_mb=0
            )
            
            self.monitor.start_monitoring(callback)
            
            # Wait for monitoring cycles
            time.sleep(0.3)
            
            self.monitor.stop_monitoring()
            
            # Monitoring should continue despite callback errors
            assert callback.call_count >= 1
    
    def test_double_start_monitoring(self):
        """Test that starting monitoring twice doesn't create multiple threads."""
        callback1 = Mock()
        callback2 = Mock()
        
        self.monitor.start_monitoring(callback1)
        first_thread = self.monitor._monitor_thread
        
        # Try to start again
        self.monitor.start_monitoring(callback2)
        
        # Should still be the same thread and first callback
        assert self.monitor._monitor_thread == first_thread
        assert self.monitor._callback == callback1
        
        self.monitor.stop_monitoring()
    
    @patch('model_management.resource_monitor.psutil.virtual_memory')
    @patch('model_management.resource_monitor.psutil.cpu_count')
    @patch('model_management.resource_monitor.psutil.cpu_percent')
    def test_get_detailed_info(self, mock_cpu_percent, mock_cpu_count, mock_memory):
        """Test detailed system information retrieval."""
        mock_memory.return_value = Mock(
            total=8 * 1024 * 1024 * 1024,
            used=4 * 1024 * 1024 * 1024,
            available=4 * 1024 * 1024 * 1024
        )
        mock_cpu_count.return_value = 8
        mock_cpu_percent.return_value = 25.5
        
        info = self.monitor.get_detailed_info()
        
        assert "timestamp" in info
        assert info["memory"]["total_mb"] == 8192
        assert info["memory"]["used_mb"] == 4096
        assert info["memory"]["usage_percent"] == 50.0
        assert info["cpu"]["count"] == 8
        assert info["cpu"]["usage_percent"] == 25.5
        assert info["monitoring"]["active"] == False
        assert info["monitoring"]["interval"] == 0.1
        assert "gpu" in info
        assert "available" in info["gpu"]
    
    def test_memory_usage_properties(self):
        """Test MemoryUsage data class properties."""
        memory_usage = MemoryUsage(
            total_mb=8192,
            used_mb=4096,
            available_mb=4096,
            gpu_total_mb=4096,
            gpu_used_mb=1024
        )
        
        assert memory_usage.usage_percentage == 50.0
        assert memory_usage.gpu_usage_percentage == 25.0
        
        # Test edge case with zero total
        zero_memory = MemoryUsage(
            total_mb=0,
            used_mb=0,
            available_mb=0,
            gpu_total_mb=0,
            gpu_used_mb=0
        )
        
        assert zero_memory.usage_percentage == 0.0
        assert zero_memory.gpu_usage_percentage == 0.0
    
    @patch('model_management.resource_monitor.logger')
    def test_monitoring_error_handling(self, mock_logger):
        """Test error handling in monitoring loop."""
        callback = Mock()
        
        with patch.object(self.monitor, 'get_memory_usage', side_effect=Exception("Memory error")):
            self.monitor.start_monitoring(callback)
            
            # Wait for monitoring cycles
            time.sleep(0.2)
            
            self.monitor.stop_monitoring()
            
            # Should have logged the error
            mock_logger.error.assert_called()
    
    def test_custom_thresholds(self):
        """Test ResourceMonitor with custom thresholds."""
        custom_monitor = ResourceMonitor(
            memory_threshold_percent=90.0,
            gpu_threshold_percent=95.0,
            monitoring_interval=1.0
        )
        
        assert custom_monitor.memory_threshold == 90.0
        assert custom_monitor.gpu_threshold == 95.0
        assert custom_monitor.monitoring_interval == 1.0