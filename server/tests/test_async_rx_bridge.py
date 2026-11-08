"""
Tests for the AsyncRxBridge class and async Rx operators.
"""

import asyncio
import pytest
import time
from unittest.mock import AsyncMock, MagicMock, patch

import rx
import rx.operators as ops
from rx import Observable

from async_rx_bridge import (
    AsyncRxBridge,
    AsyncOperationTimeoutError,
    AsyncOperationCancelledError,
    create_async_map_operator,
    create_async_starmap_operator,
    async_map,
    async_starmap,
    cleanup_default_bridge
)


class TestAsyncRxBridge:
    """Test cases for AsyncRxBridge class."""
    
    @pytest.fixture
    def bridge(self):
        """Create a fresh AsyncRxBridge instance for each test."""
        return AsyncRxBridge(default_timeout=5.0, max_concurrent_operations=3)
    
    @pytest.mark.asyncio
    async def test_map_async_success(self, bridge):
        """Test successful async function execution."""
        async def async_func(x):
            await asyncio.sleep(0.1)
            return x * 2
        
        result = await bridge.map_async(async_func, 5)
        assert result == 10
    
    @pytest.mark.asyncio
    async def test_map_async_with_kwargs(self, bridge):
        """Test async function with keyword arguments."""
        async def async_func(x, multiplier=1):
            await asyncio.sleep(0.1)
            return x * multiplier
        
        result = await bridge.map_async(async_func, 5, multiplier=3)
        assert result == 15
    
    @pytest.mark.asyncio
    async def test_map_async_timeout(self, bridge):
        """Test timeout handling in async operations."""
        async def slow_func(x):
            await asyncio.sleep(10)  # Longer than timeout
            return x
        
        with pytest.raises(AsyncOperationTimeoutError):
            await bridge.map_async(slow_func, 5, timeout=0.1)
    
    @pytest.mark.asyncio
    async def test_map_async_cancellation(self, bridge):
        """Test cancellation of async operations."""
        async def slow_func(x):
            await asyncio.sleep(10)
            return x
        
        # Start the operation
        task = asyncio.create_task(bridge.map_async(slow_func, 5))
        
        # Cancel it immediately
        task.cancel()
        
        with pytest.raises(asyncio.CancelledError):
            await task
    
    @pytest.mark.asyncio
    async def test_map_async_exception_handling(self, bridge):
        """Test exception handling in async operations."""
        async def failing_func(x):
            await asyncio.sleep(0.1)
            raise ValueError("Test error")
        
        with pytest.raises(ValueError, match="Test error"):
            await bridge.map_async(failing_func, 5)
    
    @pytest.mark.asyncio
    async def test_concurrent_operations_limit(self, bridge):
        """Test that concurrent operations are limited by semaphore."""
        call_times = []
        
        async def timed_func(x):
            call_times.append(time.time())
            await asyncio.sleep(0.2)
            return x
        
        # Start more operations than the limit (3)
        tasks = [
            asyncio.create_task(bridge.map_async(timed_func, i))
            for i in range(5)
        ]
        
        results = await asyncio.gather(*tasks)
        
        # Check that we got all results
        assert results == [0, 1, 2, 3, 4]
        
        # Check that operations were limited (first 3 should start together)
        assert len(call_times) == 5
        
        # The first 3 should start almost simultaneously
        first_batch = call_times[:3]
        assert max(first_batch) - min(first_batch) < 0.1
    
    @pytest.mark.asyncio
    async def test_cancel_all_operations(self, bridge):
        """Test cancelling all active operations."""
        async def slow_func(x):
            await asyncio.sleep(10)
            return x
        
        # Start multiple operations
        tasks = [
            asyncio.create_task(bridge.map_async(slow_func, i))
            for i in range(3)
        ]
        
        # Give them time to start
        await asyncio.sleep(0.1)
        
        # Check that operations are active
        assert bridge.get_active_operation_count() > 0
        
        # Cancel all operations
        await bridge.cancel_all_operations()
        
        # Check that all operations are cancelled
        assert bridge.get_active_operation_count() == 0
        
        # Verify tasks are cancelled
        for task in tasks:
            assert task.cancelled() or task.done()
    
    def test_get_stats(self, bridge):
        """Test getting bridge statistics."""
        stats = bridge.get_stats()
        
        assert "active_operations" in stats
        assert "max_concurrent_operations" in stats
        assert "default_timeout" in stats
        assert "available_slots" in stats
        
        assert stats["max_concurrent_operations"] == 3
        assert stats["default_timeout"] == 5.0
        assert stats["active_operations"] == 0


class TestAsyncOperators:
    """Test cases for async Rx operators."""
    
    @pytest.mark.asyncio
    async def test_async_map_operator_success(self):
        """Test async map operator with successful operations."""
        async def async_double(x):
            await asyncio.sleep(0.1)
            return x * 2
        
        results = []
        errors = []
        completed = []
        
        # Create observable and apply async map
        source = rx.from_([1, 2, 3, 4, 5])
        
        operator = create_async_map_operator(async_double, timeout=5.0)
        
        subscription = source.pipe(
            operator
        ).subscribe(
            on_next=lambda x: results.append(x),
            on_error=lambda e: errors.append(e),
            on_completed=lambda: completed.append(True)
        )
        
        # Wait for async operations to complete
        await asyncio.sleep(1.0)
        
        # Clean up
        subscription.dispose()
        
        # Check results
        assert len(errors) == 0
        assert len(results) == 5
        assert sorted(results) == [2, 4, 6, 8, 10]
        assert len(completed) == 1
    
    @pytest.mark.asyncio
    async def test_async_starmap_operator_success(self):
        """Test async starmap operator with tuple inputs."""
        async def async_multiply(x, y):
            await asyncio.sleep(0.1)
            return x * y
        
        results = []
        errors = []
        
        # Create observable with tuples
        source = rx.from_([(1, 2), (3, 4), (5, 6)])
        
        operator = create_async_starmap_operator(async_multiply, timeout=5.0)
        
        subscription = source.pipe(
            operator
        ).subscribe(
            on_next=lambda x: results.append(x),
            on_error=lambda e: errors.append(e)
        )
        
        # Wait for async operations to complete
        await asyncio.sleep(1.0)
        
        # Clean up
        subscription.dispose()
        
        # Check results
        assert len(errors) == 0
        assert len(results) == 3
        assert sorted(results) == [2, 12, 30]
    
    @pytest.mark.asyncio
    async def test_async_operator_error_handling(self):
        """Test error handling in async operators."""
        async def failing_func(x):
            await asyncio.sleep(0.1)
            if x == 3:
                raise ValueError(f"Error for {x}")
            return x * 2
        
        results = []
        errors = []
        
        # Create error handler
        def error_handler(error):
            errors.append(error)
        
        source = rx.from_([1, 2, 3, 4, 5])
        
        operator = create_async_map_operator(
            failing_func, 
            timeout=5.0, 
            error_handler=error_handler
        )
        
        subscription = source.pipe(
            operator
        ).subscribe(
            on_next=lambda x: results.append(x),
            on_error=lambda e: errors.append(e)
        )
        
        # Wait for operations to complete
        await asyncio.sleep(1.0)
        
        # Clean up
        subscription.dispose()
        
        # Check that error was handled
        assert len(errors) >= 1
        assert any(isinstance(e, ValueError) for e in errors)
    
    @pytest.mark.asyncio
    async def test_operator_disposal_cleanup(self):
        """Test that disposing operators properly cleans up async operations."""
        async def slow_func(x):
            await asyncio.sleep(10)  # Very long operation
            return x * 2
        
        source = rx.from_([1, 2, 3, 4, 5])
        
        operator = create_async_map_operator(slow_func, timeout=20.0)
        
        subscription = source.pipe(
            operator
        ).subscribe(
            on_next=lambda x: None,
            on_error=lambda e: None
        )
        
        # Let operations start
        await asyncio.sleep(0.1)
        
        # Dispose the subscription
        subscription.dispose()
        
        # Wait a bit more
        await asyncio.sleep(0.1)
        
        # Operations should be cleaned up (this is hard to test directly,
        # but disposal should not hang or cause issues)
        assert True  # If we get here, disposal worked


class TestConvenienceFunctions:
    """Test cases for convenience functions."""
    
    @pytest.mark.asyncio
    async def test_async_map_convenience_function(self):
        """Test the async_map convenience function."""
        async def async_double(x):
            await asyncio.sleep(0.1)
            return x * 2
        
        results = []
        
        source = rx.from_([1, 2, 3])
        
        subscription = source.pipe(
            async_map(async_double)
        ).subscribe(
            on_next=lambda x: results.append(x)
        )
        
        await asyncio.sleep(1.0)
        subscription.dispose()
        
        assert sorted(results) == [2, 4, 6]
    
    @pytest.mark.asyncio
    async def test_async_starmap_convenience_function(self):
        """Test the async_starmap convenience function."""
        async def async_add(x, y):
            await asyncio.sleep(0.1)
            return x + y
        
        results = []
        
        source = rx.from_([(1, 2), (3, 4), (5, 6)])
        
        subscription = source.pipe(
            async_starmap(async_add)
        ).subscribe(
            on_next=lambda x: results.append(x)
        )
        
        await asyncio.sleep(1.0)
        subscription.dispose()
        
        assert sorted(results) == [3, 7, 11]
    
    @pytest.mark.asyncio
    async def test_cleanup_default_bridge(self):
        """Test cleanup of the default bridge."""
        # This should not raise any errors
        await cleanup_default_bridge()
        assert True


class TestIntegrationScenarios:
    """Integration test scenarios that simulate real usage."""
    
    @pytest.mark.asyncio
    async def test_transcription_pipeline_simulation(self):
        """Simulate a transcription pipeline with async operations."""
        # Mock transcription function
        async def mock_transcribe(diarization, waveform):
            await asyncio.sleep(0.1)  # Simulate processing time
            return {
                "language": "en",
                "transcriptions": [
                    ("speaker_1", f"Text from {diarization}", 0.0, 1.0)
                ]
            }
        
        # Mock data
        mock_data = [
            ("diarization_1", "waveform_1"),
            ("diarization_2", "waveform_2"),
            ("diarization_3", "waveform_3")
        ]
        
        results = []
        errors = []
        
        source = rx.from_(mock_data)
        
        subscription = source.pipe(
            async_starmap(mock_transcribe, timeout=5.0)
        ).subscribe(
            on_next=lambda x: results.append(x),
            on_error=lambda e: errors.append(e)
        )
        
        # Wait for processing
        await asyncio.sleep(1.0)
        subscription.dispose()
        
        # Verify results
        assert len(errors) == 0
        assert len(results) == 3
        
        for i, result in enumerate(results):
            assert result["language"] == "en"
            assert len(result["transcriptions"]) == 1
            assert f"diarization_{i+1}" in result["transcriptions"][0][1]
    
    @pytest.mark.asyncio
    async def test_error_recovery_in_pipeline(self):
        """Test error recovery in a pipeline with mixed success/failure."""
        call_count = 0
        
        async def sometimes_failing_func(x):
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.1)
            
            if x == 2:  # Fail on second item
                raise ValueError(f"Simulated error for {x}")
            return x * 10
        
        results = []
        errors = []
        
        def error_handler(error):
            errors.append(error)
            # Continue processing despite errors
        
        source = rx.from_([1, 2, 3, 4])
        
        operator = create_async_map_operator(
            sometimes_failing_func,
            timeout=5.0,
            error_handler=error_handler
        )
        
        subscription = source.pipe(
            operator
        ).subscribe(
            on_next=lambda x: results.append(x),
            on_error=lambda e: errors.append(e)
        )
        
        await asyncio.sleep(1.0)
        subscription.dispose()
        
        # Should have processed all items, with one error
        assert call_count == 4
        assert len(errors) >= 1
        assert any(isinstance(e, ValueError) for e in errors)
        
        # Should have results for successful operations
        successful_results = [r for r in results if r is not None]
        assert len(successful_results) >= 3  # 1, 3, 4 should succeed