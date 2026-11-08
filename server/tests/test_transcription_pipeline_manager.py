"""
Tests for TranscriptionPipelineManager.
"""

import asyncio
import pytest
import uuid
from datetime import datetime, timedelta
from unittest.mock import Mock, AsyncMock, patch, MagicMock

import rx
from rx import Observable

from transcription_pipeline_manager import (
    TranscriptionPipelineManager,
    TranscriptionResult,
    ConnectionState,
    PipelineState
)
from async_rx_bridge import AsyncOperationTimeoutError, AsyncOperationCancelledError


class TestTranscriptionResult:
    """Test TranscriptionResult data class."""
    
    def test_transcription_result_creation(self):
        """Test creating a TranscriptionResult."""
        result = TranscriptionResult(
            language="en",
            transcriptions=[("speaker1", "hello world", 0.0, 2.0)],
            connection_id="test-conn",
            timestamp=datetime.now(),
            processing_time_ms=100,
            chunk_id="chunk-1"
        )
        
        assert result.language == "en"
        assert len(result.transcriptions) == 1
        assert result.transcriptions[0] == ("speaker1", "hello world", 0.0, 2.0)
        assert result.connection_id == "test-conn"
        assert result.processing_time_ms == 100
        assert result.chunk_id == "chunk-1"
    
    def test_transcription_result_to_dict(self):
        """Test converting TranscriptionResult to dictionary."""
        timestamp = datetime.now()
        result = TranscriptionResult(
            language="en",
            transcriptions=[("speaker1", "hello", 0.0, 1.0), ("speaker2", "world", 1.0, 2.0)],
            connection_id="test-conn",
            timestamp=timestamp,
            processing_time_ms=150
        )
        
        result_dict = result.to_dict()
        
        assert result_dict["language"] == "en"
        assert len(result_dict["transcriptions"]) == 2
        assert result_dict["transcriptions"][0]["speaker"] == "speaker1"
        assert result_dict["transcriptions"][0]["text"] == "hello"
        assert result_dict["transcriptions"][1]["speaker"] == "speaker2"
        assert result_dict["transcriptions"][1]["text"] == "world"
        assert result_dict["connection_id"] == "test-conn"
        assert result_dict["timestamp"] == timestamp.isoformat()
        assert result_dict["processing_time_ms"] == 150


class TestConnectionState:
    """Test ConnectionState data class."""
    
    def test_connection_state_creation(self):
        """Test creating a ConnectionState."""
        state = ConnectionState(connection_id="test-conn")
        
        assert state.connection_id == "test-conn"
        assert state.state == PipelineState.INITIALIZING
        assert state.models_loaded is False
        assert state.error_count == 0
        assert state.total_chunks_processed == 0
        assert state.successful_chunks == 0
        assert state.failed_chunks == 0
        assert state.average_processing_time_ms == 0.0
    
    def test_record_success(self):
        """Test recording successful chunk processing."""
        state = ConnectionState(connection_id="test-conn")
        
        # Record first success
        state.record_success(100)
        assert state.successful_chunks == 1
        assert state.total_chunks_processed == 1
        assert state.average_processing_time_ms == 100.0
        
        # Record second success
        state.record_success(200)
        assert state.successful_chunks == 2
        assert state.total_chunks_processed == 2
        assert state.average_processing_time_ms == 150.0  # (100 + 200) / 2
    
    def test_record_failure(self):
        """Test recording failed chunk processing."""
        state = ConnectionState(connection_id="test-conn")
        
        state.record_failure("Test error")
        assert state.failed_chunks == 1
        assert state.total_chunks_processed == 1
        assert state.error_count == 1
        assert state.last_error == "Test error"
    
    def test_get_success_rate(self):
        """Test calculating success rate."""
        state = ConnectionState(connection_id="test-conn")
        
        # No chunks processed
        assert state.get_success_rate() == 1.0
        
        # Some successes and failures
        state.record_success(100)
        state.record_success(150)
        state.record_failure("error")
        
        # 2 successes out of 3 total = 2/3 ≈ 0.667
        assert abs(state.get_success_rate() - (2/3)) < 0.001


class TestTranscriptionPipelineManager:
    """Test TranscriptionPipelineManager class."""
    
    @pytest.fixture
    def manager(self):
        """Create a TranscriptionPipelineManager for testing."""
        return TranscriptionPipelineManager(
            max_concurrent_transcriptions=2,
            transcription_timeout=5.0,
            max_error_count=3,
            buffer_cleanup_interval=60
        )
    
    @pytest.fixture
    def mock_transcriber(self):
        """Create a mock transcriber function."""
        async def transcriber(diarization, waveform, connection_id):
            return {
                "language": "en",
                "transcriptions": [("speaker1", "test text", 0.0, 1.0)]
            }
        return transcriber
    
    @pytest.fixture
    def mock_source(self):
        """Create a mock WebSocket audio source."""
        source = Mock()
        source.send = AsyncMock()
        source.stream = rx.empty()
        return source
    
    def test_manager_initialization(self, manager):
        """Test TranscriptionPipelineManager initialization."""
        assert manager.max_concurrent_transcriptions == 2
        assert manager.transcription_timeout == 5.0
        assert manager.max_error_count == 3
        assert manager.buffer_cleanup_interval == 60
        assert len(manager._connection_states) == 0
        assert len(manager._active_pipelines) == 0
    
    @pytest.mark.asyncio
    async def test_start_stop_manager(self, manager):
        """Test starting and stopping the manager."""
        assert not manager._running
        
        await manager.start()
        assert manager._running
        assert manager._cleanup_task is not None
        
        await manager.stop()
        assert not manager._running
    
    @pytest.mark.asyncio
    async def test_process_audio_chunk_success(self, manager, mock_transcriber):
        """Test successful audio chunk processing."""
        connection_id = "test-conn"
        diarization = Mock()
        waveform = Mock()
        
        await manager.start()
        
        result = await manager.process_audio_chunk(
            diarization, waveform, connection_id, mock_transcriber
        )
        
        assert result is not None
        assert isinstance(result, TranscriptionResult)
        assert result.language == "en"
        assert len(result.transcriptions) == 1
        assert result.connection_id == connection_id
        assert result.processing_time_ms >= 0
        
        # Check connection state
        state = manager.get_connection_state(connection_id)
        assert state is not None
        assert state.successful_chunks == 1
        assert state.total_chunks_processed == 1
        assert state.state == PipelineState.ACTIVE
        
        await manager.stop()
    
    @pytest.mark.asyncio
    async def test_handle_transcription_result(self, manager, mock_source):
        """Test handling transcription results."""
        connection_id = "test-conn"
        
        result = TranscriptionResult(
            language="en",
            transcriptions=[("speaker1", "hello", 0.0, 1.0), ("speaker2", "world", 1.0, 2.0)],
            connection_id=connection_id,
            timestamp=datetime.now(),
            processing_time_ms=100
        )
        
        await manager.start()
        await manager.handle_transcription_result(result, mock_source, connection_id)
        
        # Verify that send was called
        mock_source.send.assert_called_once()
        
        # Check the sent data
        sent_data = mock_source.send.call_args[0][0]
        import json
        parsed_data = json.loads(sent_data)
        
        assert len(parsed_data) == 2
        assert parsed_data[0]["speaker"] == "speaker1"
        assert parsed_data[0]["text"] == "hello"
        assert parsed_data[1]["speaker"] == "speaker2"
        assert parsed_data[1]["text"] == "world"
        
        await manager.stop()
    
    @pytest.mark.asyncio
    async def test_handle_empty_transcription_result(self, manager, mock_source):
        """Test handling empty transcription results."""
        connection_id = "test-conn"
        
        result = TranscriptionResult(
            language="en",
            transcriptions=[],  # Empty transcriptions
            connection_id=connection_id,
            timestamp=datetime.now(),
            processing_time_ms=100
        )
        
        await manager.start()
        await manager.handle_transcription_result(result, mock_source, connection_id)
        
        # Verify that send was not called for empty results
        mock_source.send.assert_not_called()
        
        await manager.stop()
    
    @pytest.mark.asyncio
    async def test_stop_pipeline(self, manager):
        """Test stopping a pipeline for a connection."""
        connection_id = "test-conn"
        
        await manager.start()
        
        # Create a connection state
        await manager._get_connection_state(connection_id)
        
        # Add a mock pipeline
        mock_disposable = Mock()
        manager._active_pipelines[connection_id] = mock_disposable
        
        # Stop the pipeline
        await manager.stop_pipeline(connection_id)
        
        # Verify cleanup
        mock_disposable.dispose.assert_called_once()
        assert connection_id not in manager._active_pipelines
        
        # Check connection state
        state = manager.get_connection_state(connection_id)
        assert state.state == PipelineState.STOPPED
        
        await manager.stop()
    
    def test_get_connection_state(self, manager):
        """Test getting connection state."""
        connection_id = "test-conn"
        
        # Initially no state
        assert manager.get_connection_state(connection_id) is None
        
        # Create state
        asyncio.run(manager._get_connection_state(connection_id))
        
        # Now should exist
        state = manager.get_connection_state(connection_id)
        assert state is not None
        assert state.connection_id == connection_id
    
    def test_get_all_connection_states(self, manager):
        """Test getting all connection states."""
        # Initially empty
        states = manager.get_all_connection_states()
        assert len(states) == 0
        
        # Add some states
        asyncio.run(manager._get_connection_state("conn1"))
        asyncio.run(manager._get_connection_state("conn2"))
        
        states = manager.get_all_connection_states()
        assert len(states) == 2
        assert "conn1" in states
        assert "conn2" in states
    
    def test_get_pipeline_statistics(self, manager):
        """Test getting pipeline statistics."""
        # Initially empty
        stats = manager.get_pipeline_statistics()
        assert stats["total_connections"] == 0
        assert stats["active_pipelines"] == 0
        assert stats["total_chunks_processed"] == 0
        
        # Add some connection states with data
        state1 = asyncio.run(manager._get_connection_state("conn1"))
        state1.record_success(100)
        state1.record_success(150)
        state1.record_failure("error")
        
        state2 = asyncio.run(manager._get_connection_state("conn2"))
        state2.record_success(200)
        
        stats = manager.get_pipeline_statistics()
        assert stats["total_connections"] == 2
        assert stats["total_chunks_processed"] == 4  # 3 from conn1 + 1 from conn2
        assert stats["total_successful_chunks"] == 3  # 2 from conn1 + 1 from conn2
        assert stats["total_failed_chunks"] == 1  # 1 from conn1
        
        # Check individual connection stats
        assert "conn1" in stats["connections"]
        assert "conn2" in stats["connections"]
        assert stats["connections"]["conn1"]["successful_chunks"] == 2
        assert stats["connections"]["conn1"]["failed_chunks"] == 1
        assert stats["connections"]["conn2"]["successful_chunks"] == 1
        assert stats["connections"]["conn2"]["failed_chunks"] == 0
    
    def test_setup_pipeline_creates_async_bridge(self, manager):
        """Test that setup_pipeline creates an async bridge for the connection."""
        connection_id = "test-conn"
        
        # Mock the required components
        mock_source = Mock()
        mock_source.stream = rx.empty()
        mock_diarizer = Mock()
        mock_transcriber = AsyncMock()
        
        # Check that async bridge is created
        assert connection_id not in manager._async_bridges
        
        # Get the async bridge (this will create it)
        bridge = manager._get_async_bridge(connection_id)
        
        # Verify bridge was created and stored
        assert connection_id in manager._async_bridges
        assert bridge is not None
        assert bridge.max_concurrent_operations == manager.max_concurrent_transcriptions
        assert bridge.default_timeout == manager.transcription_timeout
    
    @pytest.mark.asyncio
    async def test_cleanup_idle_connections(self, manager):
        """Test cleanup of idle connections."""
        await manager.start()
        
        # Create some connection states
        state1 = await manager._get_connection_state("conn1")
        state2 = await manager._get_connection_state("conn2")
        
        # Make one connection old and stopped
        old_time = datetime.now() - timedelta(minutes=15)
        state1.last_activity = old_time
        state1.state = PipelineState.STOPPED
        
        # Keep the other connection recent
        state2.update_activity()
        state2.state = PipelineState.ACTIVE
        
        # Run cleanup
        await manager._cleanup_idle_connections()
        
        # Check that only the old connection was cleaned up
        assert "conn1" not in manager._connection_states
        assert "conn2" in manager._connection_states
        
        await manager.stop()
    
if __name__ == "__main__":
    pytest.main([__file__])