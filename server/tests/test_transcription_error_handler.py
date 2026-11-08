"""
Tests for the comprehensive transcription error handler.
"""

import asyncio
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

from model_management.transcription_error_handler import (
    TranscriptionErrorHandler, TranscriptionError, TranscriptionErrorType,
    GracefulDegradationConfig, create_transcription_error_handler,
    TRANSCRIPTION_ERROR_CONFIGS
)
from model_management.circuit_breaker import CircuitBreakerConfig
from model_management.retry_handler import RetryConfig
from model_management.exceptions import (
    CircuitBreakerOpenError, RetryExhaustedError, InsufficientResourcesError
)


class TestTranscriptionErrorHandler:
    """Test cases for TranscriptionErrorHandler."""
    
    @pytest.fixture
    def error_handler(self):
        """Create a test error handler with minimal configuration."""
        circuit_config = CircuitBreakerConfig(
            failure_threshold=2,
            recovery_timeout=5,
            success_threshold=1,
            timeout=1
        )
        retry_config = RetryConfig(
            max_attempts=2,
            base_delay=0.1,
            max_delay=1.0,
            retryable_exceptions=[ConnectionError, TimeoutError, OSError, ValueError]  # Include ValueError for testing
        )
        degradation_config = GracefulDegradationConfig(
            max_consecutive_failures=2,
            failure_window_seconds=10,
            degradation_timeout_seconds=30
        )
        
        return TranscriptionErrorHandler(
            circuit_breaker_config=circuit_config,
            retry_config=retry_config,
            degradation_config=degradation_config
        )
    
    @pytest.mark.asyncio
    async def test_successful_operation(self, error_handler):
        """Test that successful operations work normally."""
        async def mock_operation():
            return {"transcriptions": [("speaker1", "hello", 0.0, 1.0)]}
        
        result = await error_handler.execute_with_protection(
            mock_operation,
            connection_id="test_conn",
            chunk_id="chunk1",
            operation_name="test_transcription"
        )
        
        assert result is not None
        assert result["transcriptions"] == [("speaker1", "hello", 0.0, 1.0)]
    
    @pytest.mark.asyncio
    async def test_timeout_error_handling(self, error_handler):
        """Test handling of timeout errors."""
        async def timeout_operation():
            await asyncio.sleep(2)  # Will timeout with 1s limit
            return {"transcriptions": []}
        
        result = await error_handler.execute_with_protection(
            timeout_operation,
            connection_id="test_conn",
            chunk_id="chunk1",
            operation_name="test_transcription"
        )
        
        # Should return None due to graceful degradation
        assert result is None
        
        # Check error tracking
        stats = error_handler.get_connection_error_stats("test_conn")
        assert stats["recent_errors"] > 0
    
    @pytest.mark.asyncio
    async def test_retry_exhausted_handling(self, error_handler):
        """Test handling when retries are exhausted."""
        call_count = 0
        
        async def failing_operation():
            nonlocal call_count
            call_count += 1
            raise ConnectionError("Network error")
        
        result = await error_handler.execute_with_protection(
            failing_operation,
            connection_id="test_conn",
            chunk_id="chunk1",
            operation_name="test_transcription"
        )
        
        # Should return None due to graceful degradation
        assert result is None
        assert call_count == 2  # Should retry once (max_attempts=2)
    
    @pytest.mark.asyncio
    async def test_circuit_breaker_protection(self, error_handler):
        """Test circuit breaker protection."""
        async def failing_operation():
            raise InsufficientResourcesError("Out of memory")
        
        # First few calls should trigger retries and failures
        for i in range(3):
            result = await error_handler.execute_with_protection(
                failing_operation,
                connection_id="test_conn",
                chunk_id=f"chunk{i}",
                operation_name="test_transcription"
            )
            assert result is None
        
        # Circuit breaker should now be open
        stats = error_handler.get_connection_error_stats("test_conn")
        assert stats["recent_errors"] >= 2
    
    @pytest.mark.asyncio
    async def test_graceful_degradation(self, error_handler):
        """Test graceful degradation when too many failures occur."""
        async def failing_operation():
            raise ValueError("Validation error")
        
        # Generate enough failures to trigger degradation
        for i in range(3):
            result = await error_handler.execute_with_protection(
                failing_operation,
                connection_id="test_conn",
                chunk_id=f"chunk{i}",
                operation_name="test_transcription"
            )
            assert result is None
        
        # Connection should now be degraded
        stats = error_handler.get_connection_error_stats("test_conn")
        assert stats["is_degraded"] is True
        
        # Subsequent operations should be skipped
        async def good_operation():
            return {"transcriptions": [("speaker1", "test", 0.0, 1.0)]}
        
        result = await error_handler.execute_with_protection(
            good_operation,
            connection_id="test_conn",
            chunk_id="chunk_new",
            operation_name="test_transcription"
        )
        assert result is None  # Should be skipped due to degradation
    
    @pytest.mark.asyncio
    async def test_connection_recovery(self, error_handler):
        """Test manual connection recovery."""
        # First degrade the connection
        async def failing_operation():
            raise ValueError("Validation error")
        
        for i in range(3):
            await error_handler.execute_with_protection(
                failing_operation,
                connection_id="test_conn",
                chunk_id=f"chunk{i}",
                operation_name="test_transcription"
            )
        
        # Verify degradation
        stats = error_handler.get_connection_error_stats("test_conn")
        assert stats["is_degraded"] is True
        
        # Recover the connection
        await error_handler.recover_connection("test_conn")
        
        # Verify recovery
        stats = error_handler.get_connection_error_stats("test_conn")
        assert stats["is_degraded"] is False
        assert stats["recent_errors"] == 0
    
    @pytest.mark.asyncio
    async def test_error_classification(self, error_handler):
        """Test error type classification."""
        # Test timeout error
        timeout_error = TranscriptionError(
            error_type=TranscriptionErrorType.TIMEOUT,
            message="Timeout",
            original_exception=asyncio.TimeoutError(),
            connection_id="test_conn"
        )
        
        classified_type = error_handler._classify_error(asyncio.TimeoutError())
        assert classified_type == TranscriptionErrorType.TIMEOUT
        
        # Test resource error
        classified_type = error_handler._classify_error(InsufficientResourcesError("No memory"))
        assert classified_type == TranscriptionErrorType.RESOURCE_EXHAUSTED
        
        # Test network error
        classified_type = error_handler._classify_error(ConnectionError("Network down"))
        assert classified_type == TranscriptionErrorType.NETWORK_ERROR
        
        # Test validation error
        classified_type = error_handler._classify_error(ValueError("Invalid input"))
        assert classified_type == TranscriptionErrorType.VALIDATION_ERROR
    
    @pytest.mark.asyncio
    async def test_success_resets_errors(self, error_handler):
        """Test that successful operations reset error tracking."""
        # Generate some failures
        async def failing_operation():
            raise ConnectionError("Network error")
        
        await error_handler.execute_with_protection(
            failing_operation,
            connection_id="test_conn",
            chunk_id="chunk1",
            operation_name="test_transcription"
        )
        
        # Verify errors are tracked
        stats = error_handler.get_connection_error_stats("test_conn")
        assert stats["recent_errors"] > 0
        
        # Now succeed
        async def success_operation():
            return {"transcriptions": [("speaker1", "success", 0.0, 1.0)]}
        
        result = await error_handler.execute_with_protection(
            success_operation,
            connection_id="test_conn",
            chunk_id="chunk2",
            operation_name="test_transcription"
        )
        
        assert result is not None
        
        # Errors should be reduced/cleared
        stats_after = error_handler.get_connection_error_stats("test_conn")
        assert stats_after["recent_errors"] <= stats["recent_errors"]
    
    @pytest.mark.asyncio
    async def test_cleanup_connection(self, error_handler):
        """Test connection cleanup."""
        # Generate some errors and degradation
        async def failing_operation():
            raise ValueError("Error")
        
        for i in range(3):
            await error_handler.execute_with_protection(
                failing_operation,
                connection_id="test_conn",
                chunk_id=f"chunk{i}",
                operation_name="test_transcription"
            )
        
        # Verify tracking exists
        stats = error_handler.get_connection_error_stats("test_conn")
        assert stats["recent_errors"] > 0
        
        # Cleanup
        await error_handler.cleanup_connection("test_conn")
        
        # Verify cleanup
        stats_after = error_handler.get_connection_error_stats("test_conn")
        assert stats_after["recent_errors"] == 0
        assert stats_after["is_degraded"] is False
    
    def test_predefined_configurations(self):
        """Test predefined error handler configurations."""
        # Test whisper configuration
        whisper_handler = create_transcription_error_handler("whisper_transcription")
        assert whisper_handler is not None
        
        # Test diarization configuration
        diarization_handler = create_transcription_error_handler("diarization")
        assert diarization_handler is not None
        
        # Test default fallback
        default_handler = create_transcription_error_handler("nonexistent")
        assert default_handler is not None
    
    def test_error_statistics(self, error_handler):
        """Test error statistics collection."""
        # Initially no stats
        all_stats = error_handler.get_all_error_stats()
        assert all_stats["total_connections_tracked"] == 0
        assert all_stats["degraded_connections"] == 0
        
        # Create some error tracking
        error_handler._connection_errors["conn1"] = [
            TranscriptionError(
                error_type=TranscriptionErrorType.TIMEOUT,
                message="Test error",
                original_exception=asyncio.TimeoutError(),
                connection_id="conn1"
            )
        ]
        error_handler._degraded_connections["conn2"] = datetime.now()
        
        # Check updated stats
        all_stats = error_handler.get_all_error_stats()
        assert all_stats["total_connections_tracked"] >= 1
        assert all_stats["degraded_connections"] >= 1
        assert "conn1" in all_stats["connections"]
        assert "conn2" in all_stats["connections"]


class TestTranscriptionErrorIntegration:
    """Integration tests for transcription error handling."""
    
    @pytest.mark.asyncio
    async def test_realistic_transcription_scenario(self):
        """Test a realistic transcription scenario with mixed success/failure."""
        error_handler = create_transcription_error_handler("whisper_transcription")
        
        success_count = 0
        failure_count = 0
        
        async def realistic_transcription(chunk_id: str):
            """Simulate realistic transcription with occasional failures."""
            nonlocal success_count, failure_count
            
            # Simulate 80% success rate
            if chunk_id.endswith(('2', '7')):  # 20% failure rate
                failure_count += 1
                if chunk_id.endswith('2'):
                    raise asyncio.TimeoutError("Model timeout")
                else:
                    raise InsufficientResourcesError("GPU memory full")
            else:
                success_count += 1
                await asyncio.sleep(0.01)  # Simulate processing time
                return {
                    "language": "en",
                    "transcriptions": [(f"speaker1", f"text for {chunk_id}", 0.0, 1.0)]
                }
        
        # Process multiple chunks
        results = []
        for i in range(10):
            chunk_id = f"chunk_{i}"
            
            # Create a proper async function for each iteration
            async def process_chunk():
                return await realistic_transcription(chunk_id)
            
            result = await error_handler.execute_with_protection(
                process_chunk,
                connection_id="realistic_conn",
                chunk_id=chunk_id,
                model_id="whisper_large",
                operation_name="transcription"
            )
            results.append(result)
        
        # Verify results
        successful_results = [r for r in results if r is not None]
        failed_results = [r for r in results if r is None]
        
        assert len(successful_results) == success_count
        assert len(failed_results) == failure_count
        assert success_count > failure_count  # Should have more successes
        
        # Check error statistics
        stats = error_handler.get_connection_error_stats("realistic_conn")
        assert stats["recent_errors"] == failure_count
        assert not stats["is_degraded"]  # Shouldn't be degraded with this failure rate
    
if __name__ == "__main__":
    pytest.main([__file__])