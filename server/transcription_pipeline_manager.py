"""
TranscriptionPipelineManager for handling async audio chunk processing.

This module provides a comprehensive pipeline manager that handles async transcription
operations with proper error handling, recovery, and connection-specific state management.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Callable, Tuple
from enum import Enum
import json

import rx
import rx.operators as ops
from rx.disposable import Disposable, CompositeDisposable

from async_rx_bridge import (
    AsyncRxBridge,
    AsyncOperationTimeoutError,
    AsyncOperationCancelledError,
)
from model_management.exceptions import ModelLoadError, InsufficientResourcesError
from model_management.transcription_error_handler import (
    TranscriptionErrorHandler,
    create_transcription_error_handler,
    TranscriptionError,
    TranscriptionErrorType,
)
from fastapiws_audio_source import FastAPIWebSocketAudioSource
import numpy as np

log = logging.getLogger(__name__)


class PipelineState(Enum):
    """States for the transcription pipeline."""

    INITIALIZING = "initializing"
    ACTIVE = "active"
    ERROR = "error"
    STOPPING = "stopping"
    STOPPED = "stopped"


@dataclass
class TranscriptionResult:
    """Result from transcription processing."""

    language: Optional[str]
    transcriptions: List[Tuple[str, str, float, float]]  # speaker, text, start, end
    connection_id: str
    timestamp: datetime
    processing_time_ms: int
    chunk_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "language": self.language,
            "transcriptions": [
                {"speaker": speaker, "text": text, "start": start, "end": end}
                for speaker, text, start, end in self.transcriptions
            ],
            "connection_id": self.connection_id,
            "timestamp": self.timestamp.isoformat(),
            "processing_time_ms": self.processing_time_ms,
            "chunk_id": self.chunk_id,
        }


@dataclass
class ConnectionState:
    """State information for a connection's transcription pipeline."""

    connection_id: str
    state: PipelineState = PipelineState.INITIALIZING
    models_loaded: bool = False
    error_count: int = 0
    last_activity: datetime = field(default_factory=datetime.now)
    buffer_size: int = 0
    total_chunks_processed: int = 0
    successful_chunks: int = 0
    failed_chunks: int = 0
    average_processing_time_ms: float = 0.0
    last_error: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)

    def update_activity(self):
        """Update the last activity timestamp."""
        self.last_activity = datetime.now()

    def record_success(self, processing_time_ms: int):
        """Record a successful chunk processing."""
        self.successful_chunks += 1
        self.total_chunks_processed += 1

        # Update average processing time
        if self.successful_chunks == 1:
            self.average_processing_time_ms = processing_time_ms
        else:
            self.average_processing_time_ms = (
                self.average_processing_time_ms * (self.successful_chunks - 1)
                + processing_time_ms
            ) / self.successful_chunks

        self.update_activity()

    def record_failure(self, error_message: str):
        """Record a failed chunk processing."""
        self.failed_chunks += 1
        self.total_chunks_processed += 1
        self.error_count += 1
        self.last_error = error_message
        self.update_activity()

    def get_success_rate(self) -> float:
        """Calculate the success rate for this connection."""
        if self.total_chunks_processed == 0:
            return 1.0
        return self.successful_chunks / self.total_chunks_processed


class TranscriptionPipelineManager:
    """
    Manages async transcription pipelines with proper error handling and recovery.

    This class handles connection-specific state management, buffer handling,
    and provides comprehensive error recovery mechanisms for transcription operations.
    """

    def __init__(
        self,
        max_concurrent_transcriptions: int = 3,
        transcription_timeout: float = 30.0,
        max_error_count: int = 10,
        buffer_cleanup_interval: int = 300,  # 5 minutes
        error_handler: Optional[TranscriptionErrorHandler] = None,
    ):
        """
        Initialize the TranscriptionPipelineManager.

        Args:
            max_concurrent_transcriptions: Maximum concurrent transcriptions per connection
            transcription_timeout: Timeout for transcription operations in seconds
            max_error_count: Maximum errors before marking connection as failed
            buffer_cleanup_interval: Interval for buffer cleanup in seconds
            error_handler: Custom error handler (creates default if None)
        """
        self.max_concurrent_transcriptions = max_concurrent_transcriptions
        self.transcription_timeout = transcription_timeout
        self.max_error_count = max_error_count
        self.buffer_cleanup_interval = buffer_cleanup_interval

        # Initialize comprehensive error handler
        self.error_handler = error_handler or create_transcription_error_handler(
            "whisper_transcription"
        )

        # Connection state tracking
        self._connection_states: Dict[str, ConnectionState] = {}
        self._connection_locks: Dict[str, asyncio.Lock] = {}
        self._active_pipelines: Dict[str, Disposable] = {}

        # Async operation management
        self._async_bridges: Dict[str, AsyncRxBridge] = {}
        self._pending_operations: Dict[str, set] = {}

        # Cleanup task
        self._cleanup_task: Optional[asyncio.Task] = None
        self._running = False

        log.info(
            f"TranscriptionPipelineManager initialized with max_concurrent={max_concurrent_transcriptions}"
        )

    async def start(self):
        """Start the pipeline manager and background tasks."""
        if self._running:
            return

        self._running = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        log.info("TranscriptionPipelineManager started")

    async def stop(self):
        """Stop the pipeline manager and cleanup all connections."""
        self._running = False

        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        # Stop all active pipelines
        for connection_id in list(self._active_pipelines.keys()):
            await self.stop_pipeline(connection_id)

        log.info("TranscriptionPipelineManager stopped")

    async def process_audio_chunk(
        self,
        audio: np.ndarray,
        diarization: List[Any],
        connection_id: str,
        transcriber: Callable,
        model_id: Optional[str] = None,
    ) -> Optional[TranscriptionResult]:
        """
        Process a single audio chunk with comprehensive async error handling.

        Args:
            diarization: Diarization result for the audio chunk
            waveform: Audio waveform data
            connection_id: ID of the connection processing this chunk
            transcriber: Async transcriber function
            model_id: ID of the model being used for transcription

        Returns:
            TranscriptionResult if successful, None if failed or gracefully degraded
        """
        import time as time_module
        
        chunk_id = str(uuid.uuid4())[:8]  # Shorter ID for logging
        start_time = datetime.now()
        
        samples = len(audio) if hasattr(audio, '__len__') else 0
        duration_sec = samples / 16000  # Assuming 16kHz
        log.debug(f"[TRANSCRIBE_START] {connection_id}: chunk={chunk_id}, {samples} samples ({duration_sec:.2f}s), speaker={diarization}")

        # Get or create connection state
        connection_state = await self._get_connection_state(connection_id)

        # Check if connection has too many errors (legacy check, now handled by error handler)
        if connection_state.error_count >= self.max_error_count:
            log.error(
                f"{connection_id} Connection has too many errors ({connection_state.error_count}), skipping chunk"
            )
            return None

        # Define the actual transcription operation
        async def transcription_operation():
            transcribe_start = time_module.perf_counter()
            result = await transcriber(audio, diarization, connection_id)
            transcribe_elapsed = (time_module.perf_counter() - transcribe_start) * 1000
            log.debug(f"[TRANSCRIBE_DONE] {connection_id}: chunk={chunk_id}, took {transcribe_elapsed:.1f}ms")
            return result

        try:
            # Execute transcription with comprehensive error handling
            transcription_data = await self.error_handler.execute_with_protection(
                transcription_operation,
                connection_id=connection_id,
                chunk_id=chunk_id,
                model_id=model_id,
                operation_name="transcription",
            )

            # Handle graceful degradation (None result)
            if transcription_data is None:
                log.debug(f"{connection_id} Chunk {chunk_id} gracefully degraded")
                return None

            # Calculate processing time
            processing_time_ms = int(
                (datetime.now() - start_time).total_seconds() * 1000
            )

            # Create transcription result
            result = TranscriptionResult(
                language=transcription_data.get("language"),
                transcriptions=transcription_data.get("transcriptions", []),
                connection_id=connection_id,
                timestamp=start_time,
                processing_time_ms=processing_time_ms,
                chunk_id=chunk_id,
            )

            # Record success
            connection_state.record_success(processing_time_ms)
            connection_state.state = PipelineState.ACTIVE
            
            num_segments = len(result.transcriptions)
            log.debug(f"[TRANSCRIBE_RESULT] {connection_id}: chunk={chunk_id}, {num_segments} segments, total_time={processing_time_ms}ms")

            return result

        except Exception as e:
            # This should rarely happen now due to comprehensive error handling
            error_msg = f"Unhandled transcription error for chunk {chunk_id}: {e}"
            log.error(f"{connection_id} {error_msg}")
            connection_state.record_failure(error_msg)

            # Set state to error if too many failures
            if connection_state.error_count >= self.max_error_count:
                connection_state.state = PipelineState.ERROR
                log.error(
                    f"{connection_id} Connection marked as error state due to excessive failures"
                )

            return None

    def setup_pipeline(
        self,
        source: FastAPIWebSocketAudioSource,
        diarizer: Callable,
        transcriber: Callable,
        connection_id: str,
    ) -> rx.Observable:
        """
        Setup the complete transcription pipeline for a connection.

        Args:
            source: Audio source stream
            diarizer: Diarization function
            transcriber: Transcription function
            connection_id: ID of the connection

        Returns:
            Observable pipeline for transcription results
        """
        from config import AppConfig

        app_config = AppConfig()

        # Get async bridge for this connection
        async_bridge = self._get_async_bridge(connection_id)

        def to_numpy(chunk):
            # Audio from the WebSocket source is float32 shaped (1, n_samples)
            return np.asarray(chunk, dtype=np.float32).reshape(-1)

        first_chunk_logged = False

        def ingest(chunks):
            """Hand audio to both models synchronously, preserving arrival order.

            Model inference happens later in the async stage; Rx async operators
            run concurrently, so ordering must not depend on them.
            """
            nonlocal first_chunk_logged
            if not first_chunk_logged:
                first_chunk_logged = True
                log.info(f"{connection_id} Pipeline processing first chunk")
            audio = np.concatenate([to_numpy(c) for c in chunks])
            track = diarizer.submit(audio, connection_id) if diarizer is not None else None
            transcriber.ingest(audio, connection_id)
            return None, track

        async_transcribe_operator = async_bridge.create_async_operator(
            self._create_transcription_wrapper(transcriber, connection_id, "nemotron"),
            timeout=self.transcription_timeout,
            error_handler=lambda err: self._handle_pipeline_error(connection_id, err),
            graceful_degradation=True,
        )

        pipeline = source.stream.pipe(
            ops.buffer_with_count(count=app_config.batch_size),
            ops.map(ingest),
            async_transcribe_operator,
            ops.filter(lambda result: result is not None),
        )

        log.debug(f"{connection_id} Transcription pipeline setup complete - batch_size={app_config.batch_size} chunks")
        return pipeline

    async def handle_transcription_result(
        self,
        result: TranscriptionResult,
        source: FastAPIWebSocketAudioSource,
        connection_id: str,
    ) -> None:
        """
        Handle a transcription result by sending it to the WebSocket.

        Args:
            result: The transcription result to handle
            source: WebSocket source to send the result to
            connection_id: ID of the connection
        """
        try:
            # Convert result to the expected format
            diarized = []
            if result.transcriptions:
                for speaker, text, start, end in result.transcriptions:
                    diarized.append(
                        {
                            "language": result.language or "",
                            "start": start,
                            "end": end,
                            "speaker": speaker,
                            "text": text,
                        }
                    )

            if diarized:
                response = json.dumps(diarized)
                await source.send(response)
                log.debug(
                    f"{connection_id} Sent transcription with {len(diarized)} segments"
                )
            else:
                log.debug(f"{connection_id} Empty transcription result, skipping send")

        except Exception as e:
            log.error(f"{connection_id} Error sending transcription result: {e}")

            # Update connection state
            connection_state = await self._get_connection_state(connection_id)
            connection_state.record_failure(f"Send error: {e}")

    async def stop_pipeline(self, connection_id: str) -> None:
        """
        Stop the transcription pipeline for a connection and cleanup resources.

        Args:
            connection_id: ID of the connection to stop
        """
        log.info(f"{connection_id} Stopping transcription pipeline")

        # Update connection state
        if connection_id in self._connection_states:
            self._connection_states[connection_id].state = PipelineState.STOPPING

        # Dispose of the pipeline
        if connection_id in self._active_pipelines:
            try:
                self._active_pipelines[connection_id].dispose()
                del self._active_pipelines[connection_id]
                log.debug(f"{connection_id} Pipeline disposed")
            except Exception as e:
                log.error(f"{connection_id} Error disposing pipeline: {e}")

        # Cancel pending async operations
        if connection_id in self._async_bridges:
            try:
                await self._async_bridges[connection_id].cancel_all_operations()
                del self._async_bridges[connection_id]
                log.debug(f"{connection_id} Async operations cancelled")
            except Exception as e:
                log.error(f"{connection_id} Error cancelling async operations: {e}")

        # Cleanup error handler tracking for this connection
        try:
            await self.error_handler.cleanup_connection(connection_id)
            log.debug(f"{connection_id} Error handler cleanup complete")
        except Exception as e:
            log.error(f"{connection_id} Error during error handler cleanup: {e}")

        # Cleanup connection state
        if connection_id in self._connection_states:
            self._connection_states[connection_id].state = PipelineState.STOPPED

        # Remove connection lock
        if connection_id in self._connection_locks:
            del self._connection_locks[connection_id]

        # Clear pending operations
        if connection_id in self._pending_operations:
            del self._pending_operations[connection_id]

        log.info(f"{connection_id} Pipeline cleanup complete")

    def get_connection_state(self, connection_id: str) -> Optional[ConnectionState]:
        """
        Get the current state for a connection.

        Args:
            connection_id: ID of the connection

        Returns:
            ConnectionState if exists, None otherwise
        """
        return self._connection_states.get(connection_id)

    def get_all_connection_states(self) -> Dict[str, ConnectionState]:
        """Get all connection states."""
        return self._connection_states.copy()

    def get_pipeline_statistics(self) -> Dict[str, Any]:
        """
        Get comprehensive statistics about all pipelines including error handling stats.

        Returns:
            Dictionary containing pipeline statistics
        """
        stats = {
            "total_connections": len(self._connection_states),
            "active_pipelines": len(self._active_pipelines),
            "connections_by_state": {},
            "total_chunks_processed": 0,
            "total_successful_chunks": 0,
            "total_failed_chunks": 0,
            "average_success_rate": 0.0,
            "connections": {},
            "error_handling": self.error_handler.get_all_error_stats(),
        }

        # Aggregate statistics
        state_counts = {}
        total_success_rate = 0.0

        for connection_id, state in self._connection_states.items():
            # Count by state
            state_name = state.state.value
            state_counts[state_name] = state_counts.get(state_name, 0) + 1

            # Aggregate totals
            stats["total_chunks_processed"] += state.total_chunks_processed
            stats["total_successful_chunks"] += state.successful_chunks
            stats["total_failed_chunks"] += state.failed_chunks
            total_success_rate += state.get_success_rate()

            # Individual connection stats with error handling info
            connection_error_stats = self.error_handler.get_connection_error_stats(
                connection_id
            )
            stats["connections"][connection_id] = {
                "state": state_name,
                "models_loaded": state.models_loaded,
                "error_count": state.error_count,
                "total_chunks": state.total_chunks_processed,
                "successful_chunks": state.successful_chunks,
                "failed_chunks": state.failed_chunks,
                "success_rate": state.get_success_rate(),
                "average_processing_time_ms": state.average_processing_time_ms,
                "last_activity": state.last_activity.isoformat(),
                "uptime_seconds": (datetime.now() - state.created_at).total_seconds(),
                "error_handling": connection_error_stats,
            }

        stats["connections_by_state"] = state_counts

        # Calculate average success rate
        if self._connection_states:
            stats["average_success_rate"] = total_success_rate / len(
                self._connection_states
            )

        return stats

    async def _get_connection_state(self, connection_id: str) -> ConnectionState:
        """
        Get or create connection state for a connection.

        Args:
            connection_id: ID of the connection

        Returns:
            ConnectionState for the connection
        """
        if connection_id not in self._connection_states:
            # Create new connection state
            self._connection_states[connection_id] = ConnectionState(
                connection_id=connection_id
            )
            self._connection_locks[connection_id] = asyncio.Lock()
            self._pending_operations[connection_id] = set()
            log.debug(f"{connection_id} Created new connection state")

        return self._connection_states[connection_id]

    def _get_async_bridge(self, connection_id: str) -> AsyncRxBridge:
        """
        Get or create async bridge for a connection.

        Args:
            connection_id: ID of the connection

        Returns:
            AsyncRxBridge for the connection
        """
        if connection_id not in self._async_bridges:
            self._async_bridges[connection_id] = AsyncRxBridge(
                default_timeout=self.transcription_timeout,
                max_concurrent_operations=self.max_concurrent_transcriptions,
            )
            log.debug(f"{connection_id} Created new async bridge")

        return self._async_bridges[connection_id]

    def _create_transcription_wrapper(
        self, transcriber: Callable, connection_id: str, model_id: Optional[str] = None
    ) -> Callable:
        """
        Create a wrapper for the transcription function that handles connection-specific logic.

        Args:
            transcriber: The original transcription function
            connection_id: ID of the connection
            model_id: ID of the model being used

        Returns:
            Wrapped transcription function
        """

        async def wrapped_transcriber(audio, diarization):
            """Wrapped transcriber with connection-specific handling."""
            return await self.process_audio_chunk(
                audio, diarization, connection_id, transcriber, model_id
            )

        return wrapped_transcriber

    def _handle_pipeline_error(self, connection_id: str, error: Exception) -> None:
        """
        Handle errors that occur in the pipeline.

        Args:
            connection_id: ID of the connection where error occurred
            error: The error that occurred
        """
        log.error(f"{connection_id} Pipeline error: {error}")

        # Update connection state if it exists
        if connection_id in self._connection_states:
            self._connection_states[connection_id].record_failure(str(error))

            # Check if we should mark the connection as error state
            if (
                self._connection_states[connection_id].error_count
                >= self.max_error_count
            ):
                self._connection_states[connection_id].state = PipelineState.ERROR
                log.error(
                    f"{connection_id} Connection marked as error state due to pipeline errors"
                )

    async def _cleanup_loop(self) -> None:
        """Background cleanup loop for idle connections and resources."""
        while self._running:
            try:
                await self._cleanup_idle_connections()
            except Exception as e:
                log.error(f"Error in cleanup loop: {e}")

            try:
                await asyncio.sleep(self.buffer_cleanup_interval)
            except asyncio.CancelledError:
                break

        log.debug("Cleanup loop stopped")

    async def _cleanup_idle_connections(self) -> None:
        """Cleanup idle connections that haven't been active recently."""
        current_time = datetime.now()
        idle_threshold = timedelta(minutes=10)  # 10 minutes idle threshold

        connections_to_cleanup = []

        for connection_id, state in self._connection_states.items():
            if current_time - state.last_activity > idle_threshold:
                if state.state in [PipelineState.STOPPED, PipelineState.ERROR]:
                    connections_to_cleanup.append(connection_id)

        for connection_id in connections_to_cleanup:
            log.info(f"{connection_id} Cleaning up idle connection")
            try:
                await self.stop_pipeline(connection_id)

                # Remove from tracking
                if connection_id in self._connection_states:
                    del self._connection_states[connection_id]

            except Exception as e:
                log.error(f"{connection_id} Error during idle cleanup: {e}")

        if connections_to_cleanup:
            log.info(f"Cleaned up {len(connections_to_cleanup)} idle connections")

    async def recover_connection(self, connection_id: str) -> bool:
        """
        Manually recover a connection from error or degraded state.

        Args:
            connection_id: ID of the connection to recover

        Returns:
            True if recovery was successful, False if connection not found
        """
        if connection_id not in self._connection_states:
            log.warning(f"{connection_id} Connection not found for recovery")
            return False

        # Reset connection state
        connection_state = self._connection_states[connection_id]
        connection_state.error_count = 0
        connection_state.last_error = None
        connection_state.state = PipelineState.ACTIVE
        connection_state.update_activity()

        # Recover from error handler degradation
        await self.error_handler.recover_connection(connection_id)

        log.info(f"{connection_id} Connection manually recovered")
        return True
