import asyncio
import gc
import json
import logging
import time
import traceback
import uuid
from contextlib import asynccontextmanager, contextmanager
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import rx
import rx.operators as ops
import torch
from config import AppConfig
from accounts import audio as accounts_audio
import log_privacy
from accounts import db as accounts_db
from accounts.auth import admin, websocket_user
from accounts.routes import router as accounts_router
from accounts.transcripts import RecordingWriter
import service_settings
from settings_routes import router as settings_router
from accounts import transcripts as accounts_transcripts
from accounts import users as accounts_users
from fastapi import Depends, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapiws_audio_source import FastAPIWebSocketAudioSource
from model_management.exceptions import ModelLoadError, InsufficientResourcesError
import telemetry
from opentelemetry.trace import StatusCode
from telemetry import instruments, tracer
from transcription_pipeline_manager import TranscriptionPipelineManager

# Configure logger with both file and console output
import os
from logging.handlers import RotatingFileHandler

# Create logs directory if it doesn't exist
# In Docker: /app/logs, locally: ../logs relative to server/
if os.path.exists("/app/logs"):
    log_dir = "/app/logs"  # Docker path
else:
    log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
os.makedirs(log_dir, exist_ok=True)

# Generate log filename with timestamp
log_filename = os.path.join(log_dir, f"mimikiku_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

# Create formatters
log_format = "%(asctime)s [%(levelname)s] %(message)s"
date_format = "%Y-%m-%d %H:%M:%S"
formatter = logging.Formatter(log_format, datefmt=date_format)

# Create handlers
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)

# Rotating file handler (50MB max, keep 5 backups)
file_handler = RotatingFileHandler(
    log_filename,
    maxBytes=50 * 1024 * 1024,  # 50MB
    backupCount=5,
    encoding='utf-8'
)
file_handler.setFormatter(formatter)

# Configure root logger
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
root_logger.handlers.clear()  # Clear any existing handlers
root_logger.addHandler(console_handler)
root_logger.addHandler(file_handler)

# Configure specific loggers - make sure they also use file handler
for logger_name in ["uvicorn", "uvicorn.error", "uvicorn.access"]:
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    logger.propagate = False  # Don't propagate to root logger

logging.getLogger("nemo_logger").setLevel(logging.ERROR)

log = logging.getLogger(__name__)
log.info(f"Logging to file: {log_filename}")


@dataclass
class QueuedRequest:
    """Represents a queued request during model loading."""

    connection_id: str
    request_data: any
    timestamp: datetime
    future: asyncio.Future


@dataclass
class ConnectionState:
    """
    State tracking for a single WebSocket connection.

    This class maintains per-connection state to enable independent connection
    management without global locks that can cause deadlocks.

    Attributes:
        connection_id: Unique identifier for the connection
        websocket: The WebSocket instance for this connection
        models_loading_event: Event to coordinate model loading for this connection
        models_loaded: Flag indicating whether models are loaded and ready
        created_at: Timestamp when the connection was created
        last_activity: Timestamp of the last activity on this connection
        error_count: Number of errors encountered on this connection
    """

    connection_id: str
    websocket: WebSocket
    models_loading_event: asyncio.Event
    models_loaded: bool
    created_at: datetime
    last_activity: datetime
    error_count: int = 0

    def mark_models_loading(self) -> None:
        """
        Mark that models are currently loading for this connection.

        This clears the models_loading_event to indicate that any waiters
        should block until models are ready.
        """
        log.debug(
            f"{self.connection_id} State transition: models_loaded={self.models_loaded} -> False, event cleared"
        )
        self.models_loaded = False
        self.models_loading_event.clear()

    def mark_models_ready(self) -> None:
        """
        Mark that models are ready for this connection.

        This sets the models_loading_event to unblock any waiters and
        updates the models_loaded flag.
        """
        log.debug(
            f"{self.connection_id} State transition: models_loaded={self.models_loaded} -> True, event set"
        )
        self.models_loaded = True
        self.models_loading_event.set()

    def is_models_loading(self) -> bool:
        """
        Check if models are currently loading for this connection.

        Returns:
            True if models are still loading, False if ready
        """
        return not self.models_loading_event.is_set()


class ConnectionManager:
    """Manages WebSocket connections and request queuing during model loading."""

    def __init__(self):
        self._active_connections: Dict[str, WebSocket] = {}
        self._connection_states: Dict[str, ConnectionState] = {}
        self._request_queues: Dict[str, List[QueuedRequest]] = {}
        self._models_loading: Dict[str, asyncio.Event] = {}

    async def connect(self, connection_id: str, websocket: WebSocket):
        """Register a new WebSocket connection."""
        log.debug(f"{connection_id} Starting connection registration")

        await websocket.accept()
        self._active_connections[connection_id] = websocket
        self._request_queues[connection_id] = []
        log.debug(f"{connection_id} WebSocket accepted and queues initialized")

        # Create ConnectionState for this connection
        models_loading_event = asyncio.Event()
        connection_state = ConnectionState(
            connection_id=connection_id,
            websocket=websocket,
            models_loading_event=models_loading_event,
            models_loaded=False,
            created_at=datetime.now(),
            last_activity=datetime.now(),
            error_count=0,
        )
        self._connection_states[connection_id] = connection_state
        log.debug(f"{connection_id} ConnectionState created - models_loaded=False")

        # Create connection-specific event in _models_loading
        self._models_loading[connection_id] = models_loading_event
        log.debug(f"{connection_id} Connection-specific models_loading event created")

        log.info(
            f"{connection_id} WebSocket connected - total active connections: {len(self._active_connections)}"
        )

    async def disconnect(self, connection_id: str):
        """
        Unregister a WebSocket connection and perform comprehensive cleanup.

        This method ensures all connection-specific resources are properly cleaned up,
        including unblocking any waiters and removing all state tracking. All cleanup
        operations are protected with try-except blocks to ensure cleanup continues
        even if individual steps fail.

        Args:
            connection_id: ID of the connection to disconnect
        """
        log.info(f"{connection_id} Starting disconnect cleanup")

        # Track cleanup steps for detailed logging
        cleanup_steps = []

        try:
            # Step 1: Set models_loading_event to unblock any waiters before cleanup
            if connection_id in self._connection_states:
                try:
                    state = self._connection_states[connection_id]
                    state.models_loading_event.set()
                    cleanup_steps.append("unblocked_waiters")
                    log.debug(
                        f"{connection_id} Set models_loading_event to unblock any waiters"
                    )
                except Exception as e:
                    log.error(f"{connection_id} Error unblocking waiters: {e}")

            # Step 2: Cancel any queued requests
            if connection_id in self._request_queues:
                try:
                    cancelled_count = 0
                    for queued_request in self._request_queues[connection_id]:
                        if not queued_request.future.done():
                            queued_request.future.cancel()
                            cancelled_count += 1
                    del self._request_queues[connection_id]
                    cleanup_steps.append(f"cancelled_{cancelled_count}_requests")
                    log.debug(
                        f"{connection_id} Cancelled {cancelled_count} queued requests"
                    )
                except Exception as e:
                    log.error(f"{connection_id} Error cancelling queued requests: {e}")

            # Step 3: Remove connection from _connection_states dictionary
            if connection_id in self._connection_states:
                try:
                    del self._connection_states[connection_id]
                    cleanup_steps.append("removed_connection_state")
                    log.debug(f"{connection_id} Removed connection state")
                except Exception as e:
                    log.error(f"{connection_id} Error removing connection state: {e}")

            # Step 4: Remove connection-specific entry from _models_loading dictionary
            if connection_id in self._models_loading:
                try:
                    del self._models_loading[connection_id]
                    cleanup_steps.append("removed_models_loading_event")
                    log.debug(f"{connection_id} Removed models loading event")
                except Exception as e:
                    log.error(
                        f"{connection_id} Error removing models loading event: {e}"
                    )

            # Step 5: Remove from active connections
            if connection_id in self._active_connections:
                try:
                    del self._active_connections[connection_id]
                    cleanup_steps.append("removed_active_connection")
                    log.debug(f"{connection_id} Removed from active connections")
                except Exception as e:
                    log.error(f"{connection_id} Error removing active connection: {e}")

            # Log successful completion with cleanup summary
            log.info(
                f"{connection_id} WebSocket disconnected - cleanup steps: {', '.join(cleanup_steps)}"
            )

        except Exception as e:
            # Ensure we log any unexpected errors during cleanup
            log.error(
                f"{connection_id} Unexpected error during disconnect cleanup: {e}"
            )
            log.error(
                f"{connection_id} Completed cleanup steps before error: {', '.join(cleanup_steps)}"
            )

    async def queue_request_during_loading(
        self, connection_id: str, request_data: any
    ) -> any:
        """Queue a request if models are loading, otherwise process immediately."""
        # Check if models are currently loading for this specific connection
        if (
            connection_id in self._models_loading
            and not self._models_loading[connection_id].is_set()
        ):
            queue_size = len(self._request_queues.get(connection_id, []))
            log.info(
                f"{connection_id} Queueing request while models are loading - current queue size: {queue_size}"
            )

            # Create a future for this request
            future = asyncio.Future()
            queued_request = QueuedRequest(
                connection_id=connection_id,
                request_data=request_data,
                timestamp=datetime.now(),
                future=future,
            )

            # Add to queue
            if connection_id not in self._request_queues:
                self._request_queues[connection_id] = []
            self._request_queues[connection_id].append(queued_request)
            log.debug(
                f"{connection_id} Request queued - new queue size: {len(self._request_queues[connection_id])}"
            )

            # Wait for the request to be processed
            try:
                return await future
            except asyncio.CancelledError:
                log.info(f"{connection_id} Queued request was cancelled")
                return None

        # Models are ready, process immediately
        log.debug(f"{connection_id} Models ready, processing request immediately")
        return await self._process_request_immediately(connection_id, request_data)

    async def _process_request_immediately(
        self, connection_id: str, request_data: any
    ) -> any:
        """Process a request immediately when models are ready."""
        # This will be implemented to call the actual transcription pipeline
        return request_data

    async def notify_models_loading(self, connection_id: str):
        """
        Notify that models are starting to load for a specific connection.

        Args:
            connection_id: ID of the connection that is loading models
        """
        log.debug(f"{connection_id} notify_models_loading called")

        # Create connection-specific Event in _models_loading if not exists
        if connection_id not in self._models_loading:
            self._models_loading[connection_id] = asyncio.Event()
            log.debug(f"{connection_id} Created new models_loading event")

        # Update ConnectionState to mark models as loading
        if connection_id in self._connection_states:
            self._connection_states[connection_id].mark_models_loading()
            log.debug(f"{connection_id} ConnectionState updated to loading state")
        else:
            log.warning(
                f"{connection_id} ConnectionState not found when marking models as loading"
            )

        # Add logging for model loading start per connection
        log.info(
            f"{connection_id} Models loading started - connection waiting for models"
        )

    async def notify_models_ready(self, connection_id: str):
        """
        Notify that models are ready for a specific connection.

        Args:
            connection_id: ID of the connection whose models are ready
        """
        log.debug(f"{connection_id} notify_models_ready called")

        # Set connection-specific Event in _models_loading
        if connection_id in self._models_loading:
            self._models_loading[connection_id].set()
            log.debug(f"{connection_id} models_loading event set - unblocking waiters")
        else:
            log.warning(
                f"{connection_id} models_loading event not found when marking models as ready"
            )

        # Update ConnectionState to mark models as ready
        if connection_id in self._connection_states:
            self._connection_states[connection_id].mark_models_ready()
            log.debug(f"{connection_id} ConnectionState updated to ready state")
        else:
            log.warning(
                f"{connection_id} ConnectionState not found when marking models as ready"
            )

        # Add logging for model loading completion per connection
        log.info(f"{connection_id} Models ready and loaded - connection can proceed")

    async def wait_for_models(self, connection_id: str, timeout: float = 30.0) -> bool:
        """
        Wait for models to be ready with timeout protection.

        This method provides timeout protection to prevent indefinite blocking
        when waiting for models to load. It returns a boolean indicating whether
        models became ready within the timeout period.

        Args:
            connection_id: ID of the connection to wait for
            timeout: Maximum time to wait in seconds (default: 30.0)

        Returns:
            True if models are ready within timeout, False otherwise
        """
        log.debug(f"{connection_id} wait_for_models called with timeout={timeout}s")

        # Check if connection exists
        if connection_id not in self._connection_states:
            log.warning(
                f"{connection_id} Connection not found in wait_for_models - connection may have been disconnected"
            )
            return False

        state = self._connection_states[connection_id]

        # If models are already loaded, return immediately
        if state.models_loaded:
            log.debug(f"{connection_id} Models already loaded, returning immediately")
            return True

        log.info(f"{connection_id} Waiting for models to load (timeout={timeout}s)")
        start_time = time.time()

        try:
            # Wait for the models_loading_event with timeout
            await asyncio.wait_for(state.models_loading_event.wait(), timeout=timeout)
            elapsed = time.time() - start_time
            log.info(
                f"{connection_id} Models became ready within timeout - elapsed time: {elapsed:.2f}s"
            )
            return True

        except asyncio.TimeoutError:
            elapsed = time.time() - start_time
            log.error(
                f"{connection_id} TIMEOUT waiting for models to load - "
                f"timeout={timeout}s, elapsed={elapsed:.2f}s, "
                f"models_loaded={state.models_loaded}, "
                f"event_is_set={state.models_loading_event.is_set()}"
            )
            return False
        except Exception as e:
            elapsed = time.time() - start_time
            log.error(
                f"{connection_id} Error waiting for models - "
                f"elapsed={elapsed:.2f}s, error={type(e).__name__}: {e}"
            )
            return False

    def is_loading_models(self, connection_id: str) -> bool:
        """
        Check if models are currently loading for a specific connection.

        This method provides a non-blocking way to check the loading state
        of models for a given connection.

        Args:
            connection_id: ID of the connection to check

        Returns:
            True if models are currently loading, False if ready or connection not found
        """
        # Check if connection exists
        if connection_id not in self._connection_states:
            log.debug(
                f"{connection_id} Connection not found in is_loading_models - may have been disconnected"
            )
            return False

        state = self._connection_states[connection_id]
        is_loading = state.is_models_loading()

        log.debug(
            f"{connection_id} Model loading state check: "
            f"is_loading={is_loading}, models_loaded={state.models_loaded}, "
            f"event_is_set={state.models_loading_event.is_set()}"
        )
        return is_loading

    def get_connection_count(self) -> int:
        """Get the number of active connections."""
        return len(self._active_connections)

    def get_queue_status(self) -> Dict[str, int]:
        """Get the status of request queues."""
        return {
            connection_id: len(queue)
            for connection_id, queue in self._request_queues.items()
        }


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await startup_event()
    try:
        yield
    finally:
        await shutdown_event()


app = FastAPI(lifespan=lifespan)
app_config = AppConfig()
connection_manager = ConnectionManager()
pipeline_manager = TranscriptionPipelineManager()
app.include_router(accounts_router)
app.include_router(settings_router)
instance = service_settings.Instance()
if telemetry.start(app):
    def _resource(key: str, scale: float = 1.0):
        def read():
            value = app_config.model_manager.get_resource_snapshot().get(key)
            return None if value is None else value * scale
        return read

    telemetry.observe("mimikiku.gpu.memory.used", "By", "GPU memory in use (whole GPU)", _resource("gpu_used_mb", 2**20))
    telemetry.observe("mimikiku.gpu.memory.total", "By", "GPU memory size", _resource("gpu_total_mb", 2**20))
    telemetry.observe("mimikiku.ram.used", "By", "Host RAM in use", _resource("ram_used_mb", 2**20))

# connection_id -> email of the user streaming on it, for the monitoring page.
connection_users: Dict[str, str] = {}

# Monitoring and model controls are admin-only.
admin_only = [Depends(admin)]


async def startup_event():
    """Initialize the model manager and pipeline manager on startup."""
    # No Mongo, no service: fail the start rather than run without auth.
    accounts_db.connect()
    await accounts_db.init()
    await accounts_users.seed_admin(
        os.environ.get("MIMIKIKU_ADMIN_EMAIL"), os.environ.get("MIMIKIKU_ADMIN_PASSWORD")
    )
    if accounts_audio.enabled():
        log.info("STORE_AUDIO is on: recording audio is kept with transcripts")
    if log_privacy.enabled():
        log.warning("LOG_TRANSCRIPTS is on: transcript text goes to the logs")
    # Model-level settings must be in place before the diarizer preload below.
    settings_values, settings_version = await service_settings.load()
    app_config.apply_restart_settings(settings_values)
    await instance.register(
        settings_values, settings_version, connection_manager.get_connection_count
    )
    try:
        await app_config.start_model_manager()
        await pipeline_manager.start()

        # Pre-load models to ensure stable CUDA initialization before any connections
        log.info("Pre-loading models on startup for stable CUDA initialization...")
        try:
            # We use sequential loading here too
            await app_config.get_subprocess_diarizer()
            # User requested to disable transcriber
            # await app_config.get_nemotron_asr()
            log.info("Models pre-loaded successfully on startup")
        except Exception as e:
            log.error(f"Failed to pre-load models: {e}")
            # Don't raise here, allow the server to start even if pre-loading fails
            # (they will try to load again on first connection)

        log.info("Model manager and pipeline manager started successfully")
    except Exception as e:
        log.error(f"Failed to start managers: {e}")
        raise


async def shutdown_event():
    """Cleanup the model manager and pipeline manager on shutdown."""
    await instance.stop()
    try:
        await pipeline_manager.stop()
        await app_config.stop_model_manager()
        log.info("Pipeline manager and model manager stopped successfully")
    except Exception as e:
        log.error(f"Error stopping managers: {e}")
    telemetry.shutdown()  # last, so the shutdown logs above are exported


@app.get("/")
async def get_html():
    return FileResponse("../webui/dist/index.html", media_type="text/html")


@app.get("/api/health")
async def health():
    """Unauthenticated liveness check (Docker healthcheck)."""
    return {"ok": True}


@app.get("/api/models/status", dependencies=admin_only)
async def get_model_status():
    """Get the status of all loaded models."""
    try:
        status = await app_config.model_manager.get_model_status()
        pipeline_stats = pipeline_manager.get_pipeline_statistics()
        return {
            "models": status,
            "connections": connection_manager.get_connection_count(),
            "queues": connection_manager.get_queue_status(),
            "pipelines": pipeline_stats,
        }
    except Exception as e:
        log.error(f"Error getting model status: {e}")
        return {"error": str(e)}


@app.get("/api/models/metrics", dependencies=admin_only)
async def get_model_metrics():
    """Get performance metrics for the model management system."""
    try:
        metrics = app_config.model_manager.get_performance_metrics()
        # psutil / nvidia-smi calls block, so keep them off the event loop
        resources = await asyncio.to_thread(
            app_config.model_manager.get_resource_snapshot
        )
        return {**metrics, "resources": resources}
    except Exception as e:
        log.error(f"Error getting model metrics: {e}")
        return {"error": str(e)}


@app.post("/api/models/cleanup", dependencies=admin_only)
async def trigger_model_cleanup():
    """Manually trigger model cleanup."""
    try:
        # Get status before cleanup
        status_before = await app_config.model_manager.get_model_status()
        models_before = list(status_before.keys())

        # Perform cleanup
        await app_config.model_manager.cleanup_idle_models()

        # Get status after cleanup
        status_after = await app_config.model_manager.get_model_status()
        models_after = list(status_after.keys())

        # Calculate what was cleaned up
        cleaned_models = [m for m in models_before if m not in models_after]
        protected_models = (
            app_config.model_manager._websocket_session_tracker.get_protected_models()
        )

        return {
            "message": "Model cleanup completed",
            "models_before": len(models_before),
            "models_after": len(models_after),
            "cleaned_count": len(cleaned_models),
            "cleaned_models": cleaned_models,
            "protected_models": list(protected_models),
        }
    except Exception as e:
        log.error(f"Error during model cleanup: {e}")
        return {"error": str(e)}


@app.get("/api/pipelines/status", dependencies=admin_only)
async def get_pipeline_status():
    """Get detailed status of all transcription pipelines."""
    try:
        stats = pipeline_manager.get_pipeline_statistics()
        for connection_id, conn in stats.get("connections", {}).items():
            conn["user"] = connection_users.get(connection_id)
        return stats
    except Exception as e:
        log.error(f"Error getting pipeline status: {e}")
        return {"error": str(e)}


@app.get("/api/pipelines/connections/{connection_id}", dependencies=admin_only)
async def get_connection_status(connection_id: str):
    """Get status for a specific connection."""
    try:
        connection_state = pipeline_manager.get_connection_state(connection_id)
        if connection_state is None:
            return {"error": "Connection not found"}

        return {
            "connection_id": connection_id,
            "state": connection_state.state.value,
            "models_loaded": connection_state.models_loaded,
            "error_count": connection_state.error_count,
            "total_chunks": connection_state.total_chunks_processed,
            "successful_chunks": connection_state.successful_chunks,
            "failed_chunks": connection_state.failed_chunks,
            "success_rate": connection_state.get_success_rate(),
            "average_processing_time_ms": connection_state.average_processing_time_ms,
            "last_activity": connection_state.last_activity.isoformat(),
            "last_error": connection_state.last_error,
            "uptime_seconds": (
                datetime.now() - connection_state.created_at
            ).total_seconds(),
        }
    except Exception as e:
        log.error(f"Error getting connection status: {e}")
        return {"error": str(e)}


@app.get("/api/websocket/sessions", dependencies=admin_only)
async def get_websocket_sessions():
    """Get WebSocket session statistics and protection status."""
    try:
        session_tracker = app_config.model_manager.get_websocket_session_tracker()
        return session_tracker.get_session_stats()
    except Exception as e:
        log.error(f"Error getting WebSocket session stats: {e}")
        return {"error": str(e)}


async def get_models_with_loading_notification(connection_id: str):
    """
    Get models with proper per-connection notifications and timeout protection.

    This function gets the diarizer and transcriber models, reusing already-loaded
    models when possible to avoid redundant loading on reconnections. It includes
    connection-specific state tracking, timeout protection, and comprehensive error handling.

    Args:
        connection_id: ID of the connection requesting models

    Returns:
        Tuple of (diarizer, transcriber) models

    Raises:
        asyncio.TimeoutError: If model loading exceeds 30-second timeout
        ModelLoadError: If model loading fails
        Exception: For other unexpected errors during model loading
    """
    log.debug(f"{connection_id} get_models_with_loading_notification called")
    start_time = time.time()

    try:
        # Notify that models are loading for this specific connection
        await connection_manager.notify_models_loading(connection_id)

        log.info(f"{connection_id} Getting models (will reuse if already loaded)")

        # Wrap model loading in asyncio.wait_for() with 30-second timeout
        # The get_diarizer() and get_whisper_transcriber() methods will reuse
        # already-loaded models if available, avoiding redundant loading
        async def _load_both():
            # Use subprocess diarizer to avoid CUDA conflicts with ASR
            d = await app_config.get_subprocess_diarizer()
            t = await app_config.get_nemotron_asr()
            return d, t

        try:
            diarizer, transcriber = await asyncio.wait_for(
                _load_both(),
                timeout=60.0,
            )

            elapsed = time.time() - start_time
            log.info(f"{connection_id} Models ready - elapsed time: {elapsed:.2f}s")
            instruments.model_load_duration.record(elapsed, {"outcome": "ok"})
            return diarizer, transcriber

        except asyncio.TimeoutError:
            elapsed = time.time() - start_time
            instruments.model_load_duration.record(elapsed, {"outcome": "timeout"})
            # Add error logging for model loading timeout with detailed context
            log.error(
                f"{connection_id} MODEL LOADING TIMEOUT - "
                f"timeout=30.0s, elapsed={elapsed:.2f}s, "
                f"This may indicate resource constraints or slow model initialization"
            )
            raise ModelLoadError(
                f"Model loading timeout for connection {connection_id}"
            )

        except Exception as e:
            elapsed = time.time() - start_time
            # Add error logging for model loading failures with detailed context
            log.error(
                f"{connection_id} Failed to get models - "
                f"elapsed={elapsed:.2f}s, error={type(e).__name__}: {e}"
            )
            raise

    except Exception as e:
        elapsed = time.time() - start_time
        # Add error logging for any unexpected errors
        log.error(
            f"{connection_id} Error in get_models_with_loading_notification - "
            f"elapsed={elapsed:.2f}s, error={type(e).__name__}: {e}"
        )
        raise

    finally:
        # Ensure notify_models_ready() is always called to unblock any waiters
        # This is critical to prevent connections from waiting indefinitely
        elapsed = time.time() - start_time
        await connection_manager.notify_models_ready(connection_id)
        log.debug(
            f"{connection_id} Models ready notification sent in finally block - total elapsed: {elapsed:.2f}s"
        )


async def _refuse(websocket: WebSocket, code: int, reason: str) -> None:
    """Accept then close, so browsers see the code and reason (a rejected handshake is just 1006)."""
    await websocket.accept()
    await websocket.close(code=code, reason=reason)


@app.websocket("/listen")
async def websocket_endpoint(websocket: WebSocket):
    """Stream audio in, segments out. Query parameters:

    - `events=1`: also send `{"type": "status"|"transcript", ...}` messages.
    - `transcript=<id>`: add this recording to one of your transcripts
      (default: start a new transcript with the first segments).
    - `save=0`: don't save anything (mimikiku-capture keeping its transcript local).
    """
    connection_id = str(uuid.uuid4())
    subscription = None
    diarizer = None
    transcriber = None

    log.info(f"{connection_id} New WebSocket connection request received")

    principal, reason = await websocket_user(websocket)
    if not principal:
        log.info(f"{connection_id} Refused: {reason}")
        instruments.refused.add(1, {"reason": "cross_origin" if "origin" in reason else "unauthorized"})
        await _refuse(websocket, 4401, reason)
        return

    params = websocket.query_params
    transcript_id = params.get("transcript") or None
    if transcript_id and not await accounts_transcripts.exists(principal.id, transcript_id):
        instruments.refused.add(1, {"reason": "not_found"})
        await _refuse(websocket, 4404, "Transcript not found")
        return
    writer = RecordingWriter(principal.id, transcript_id) if params.get("save") != "0" else None
    upload = None
    if writer and accounts_audio.enabled():
        upload = accounts_audio.AudioUpload(principal.id, writer.recording["id"], app_config.sample_rate)

    connection_users[connection_id] = principal.user["email"]
    attrs = {"client": principal.via, "saved": writer is not None}
    instruments.recordings.add(1, attrs)
    instruments.recordings_active.add(1)
    # Connect to ready (auth, model loading, pipeline setup); the stream itself isn't a span.
    connect_span = tracer.start_span(
        "listen.connect",
        attributes={
            "mimikiku.client": principal.via,
            "mimikiku.saved": writer is not None,
            "mimikiku.connection_id": connection_id,
            "enduser.id": principal.id,
        },
    )
    source = None
    log.info(
        f"{connection_id} User {principal.user['email']} via {principal.via}, "
        f"transcript={transcript_id or 'new'}, save={writer is not None}, audio={upload is not None}"
    )

    try:
        # Register the connection with connection manager
        log.debug(f"{connection_id} Registering connection with ConnectionManager")
        await connection_manager.connect(connection_id, websocket)
        log.debug(f"{connection_id} Connection registered successfully")

        # Recover any previous degraded state for this connection
        # This ensures fresh connections start with a clean slate
        try:
            error_handler = pipeline_manager.error_handler
            if error_handler:
                await error_handler.recover_connection(connection_id)
                log.debug(f"{connection_id} Recovered from any previous degraded state")
        except Exception as recovery_error:
            log.debug(
                f"{connection_id} Error during degradation recovery (non-critical): {recovery_error}"
            )

        # Clients that pass ?events=1 get {"type": "status", ...} messages
        # alongside segment arrays; others only get segments.
        wants_events = params.get("events") == "1"

        async def send_status(state: str) -> None:
            if wants_events:
                await websocket.send_text(json.dumps({"type": "status", "state": state}))

        # Create WebSocket audio source
        log.debug(f"{connection_id} Creating WebSocket audio source")
        source = FastAPIWebSocketAudioSource(
            app_config.sample_rate, websocket, tap=upload.write if upload else None
        )
        await send_status("loading")

        # Load models with proper notification and queuing
        # Check if models are already loaded to avoid redundant loading on reconnections
        log.debug(
            f"{connection_id} Requesting models (checking for existing models first)"
        )
        try:
            diarizer, transcriber = await get_models_with_loading_notification(
                connection_id
            )
            log.info(f"{connection_id} Models ready, proceeding with pipeline setup")
        except Exception as e:
            log.error(
                f"{connection_id} Failed to get models, closing connection: {type(e).__name__}: {e}"
            )
            instruments.refused.add(1, {"reason": "models"})
            connect_span.set_status(StatusCode.ERROR, "Model loading failed")
            await websocket.close(code=1011, reason="Model loading failed")
            return

        # Update connection state to indicate models are loaded
        log.debug(f"{connection_id} Updating pipeline manager connection state")
        connection_state = await pipeline_manager._get_connection_state(connection_id)
        connection_state.models_loaded = True
        log.debug(
            f"{connection_id} Pipeline connection state updated - models_loaded=True"
        )

        # Register WebSocket session with model manager for session protection
        try:
            # Get model IDs from the loaded models
            model_ids = set()
            if hasattr(diarizer, "model_id"):
                model_ids.add(diarizer.model_id)
            if hasattr(transcriber, "model_id"):
                model_ids.add(transcriber.model_id)

            # Register the session with the model manager
            if model_ids:
                app_config.model_manager.register_websocket_session(
                    connection_id, model_ids
                )
                log.info(
                    f"{connection_id} Registered WebSocket session with models: {model_ids}"
                )
            else:
                log.warning(
                    f"{connection_id} No model IDs found for WebSocket session registration"
                )
        except Exception as e:
            log.warning(
                f"{connection_id} Failed to register WebSocket session: {type(e).__name__}: {e}"
            )

        # This recording's endpointing and speaker settings (admin-tunable, see
        # service_settings.py); later changes apply to later recordings.
        if hasattr(transcriber, "open_session"):
            try:
                values, _ = await service_settings.load()
            except Exception as e:
                log.warning(f"{connection_id} Could not read service settings, using defaults: {e}")
                values = service_settings.defaults()
            transcriber.open_session(
                connection_id,
                app_config.recording_endpoint(service_settings.recording_settings(values)),
            )

        # Setup the transcription pipeline using TranscriptionPipelineManager
        log.debug(f"{connection_id} Setting up transcription pipeline")
        pipeline = pipeline_manager.setup_pipeline(
            source=source,
            diarizer=diarizer,
            transcriber=transcriber,
            connection_id=connection_id,
        )
        log.debug(f"{connection_id} Transcription pipeline setup complete")

        # Subscribe to the pipeline with proper error handling and result processing
        log.debug(f"{connection_id} Subscribing to transcription pipeline")
        subscription = pipeline.subscribe(
            on_next=lambda result: asyncio.create_task(
                _handle_transcription_result_wrapper(
                    result, source, connection_id, writer, wants_events
                )
            ),
            on_error=lambda err: asyncio.create_task(
                handle_pipeline_error(connection_id, err)
            ),
            on_completed=lambda: log.info(f"{connection_id} Pipeline completed"),
        )

        # Store the subscription in pipeline manager for cleanup
        pipeline_manager._active_pipelines[connection_id] = subscription
        log.debug(f"{connection_id} Pipeline subscription stored in pipeline manager")

        # Signal that pipeline is ready to receive audio
        source.set_ready()
        await send_status("ready")
        connect_span.end()

        log.info(
            f"{connection_id} Transcription pipeline started - ready to receive audio"
        )

        # Start reading from the WebSocket
        await source.read()

    except WebSocketDisconnect:
        log.info(f"{connection_id} WebSocket disconnected by client")
    except Exception as e:
        log.error(f"{connection_id} WebSocket error: {type(e).__name__}: {e}")
        log.debug(f"{connection_id} WebSocket error traceback:", exc_info=True)

        # Report error to pipeline manager
        connection_state = pipeline_manager.get_connection_state(connection_id)
        if connection_state:
            connection_state.record_failure(f"WebSocket error: {e}")
            log.debug(f"{connection_id} Error recorded in pipeline connection state")
    finally:
        if connect_span.is_recording():
            connect_span.end()
        instruments.recordings_active.add(-1)
        if source is not None and source.samples_received:
            seconds = source.samples_received / app_config.sample_rate
            instruments.audio.add(seconds, attrs)
            instruments.recording_duration.record(seconds, attrs)

        # Comprehensive cleanup using TranscriptionPipelineManager
        try:
            log.info(f"{connection_id} Starting connection cleanup in finally block")
            cleanup_start_time = time.time()

            # Disconnect WebSocket session from model manager
            try:
                log.debug(
                    f"{connection_id} Disconnecting WebSocket session from model manager"
                )
                app_config.model_manager.disconnect_websocket_session(connection_id)
                log.info(
                    f"{connection_id} WebSocket session disconnected from model manager"
                )
            except Exception as session_error:
                log.warning(
                    f"{connection_id} Error disconnecting WebSocket session: {type(session_error).__name__}: {session_error}"
                )

            # Stop the transcription pipeline (handles async operation cleanup)
            log.debug(f"{connection_id} Stopping transcription pipeline")
            await pipeline_manager.stop_pipeline(connection_id)
            log.debug(f"{connection_id} Transcription pipeline stopped")

            # Clear diarizer session state
            if diarizer and hasattr(diarizer, "cleanup_session"):
                log.debug(f"{connection_id} Cleaning up diarizer session state")
                diarizer.cleanup_session(connection_id)

            # Clear transcriber buffer or session state if it exists
            if "transcriber" in locals():
                if hasattr(transcriber, "cleanup_session"):
                    try:
                        log.debug(f"{connection_id} Cleaning up ASR session state")
                        transcriber.cleanup_session(connection_id)
                        log.debug(f"{connection_id} ASR session state cleaned up")
                    except Exception as asr_cleanup_error:
                        log.error(
                            f"{connection_id} Error cleaning up ASR session state: {asr_cleanup_error}"
                        )

            # Unregister the connection from connection manager
            log.debug(
                f"{connection_id} Unregistering connection from ConnectionManager"
            )
            await connection_manager.disconnect(connection_id)
            connection_users.pop(connection_id, None)

            if upload:
                # Keep the audio only if its recording made it into a transcript.
                if await upload.finish(keep=writer.saved) and not await writer.attach_audio(
                    upload.file_id
                ):
                    await accounts_audio.delete_files([upload.file_id])

            cleanup_elapsed = time.time() - cleanup_start_time
            log.info(
                f"{connection_id} Connection cleanup completed - elapsed time: {cleanup_elapsed:.2f}s"
            )

        except Exception as cleanup_error:
            log.error(
                f"{connection_id} Error during cleanup: {type(cleanup_error).__name__}: {cleanup_error}"
            )
            log.debug(f"{connection_id} Cleanup error traceback:", exc_info=True)


async def _handle_transcription_result_wrapper(
    result: Any,
    source: FastAPIWebSocketAudioSource,
    connection_id: str,
    writer: Optional[RecordingWriter] = None,
    wants_events: bool = False,
) -> None:
    """
    Wrapper function to convert TranscriptionResult to dictionary format and call send_transcription.

    This function bridges the gap between the pipeline manager's TranscriptionResult objects
    and the send_transcription function's expected dictionary format.

    Args:
        result: TranscriptionResult object from the pipeline
        source: WebSocket audio source
        connection_id: ID of the connection
    """
    try:
        # Handle None results (from error recovery)
        if result is None:
            log.debug(f"{connection_id} Received None result from pipeline, skipping")
            return

        if getattr(result, "processing_time_ms", None) is not None:
            instruments.transcribe_duration.record(result.processing_time_ms / 1000)

        # Convert TranscriptionResult to dictionary format
        if hasattr(result, "language") and hasattr(result, "transcriptions"):
            # It's a TranscriptionResult object
            transcription_dict = {
                "language": result.language,
                "transcriptions": result.transcriptions,
            }
        elif isinstance(result, dict):
            # It's already a dictionary
            transcription_dict = result
        else:
            log.error(f"{connection_id} Unexpected result type: {type(result)}")
            return

        await send_transcription(
            source, transcription_dict, connection_id, writer, wants_events
        )

    except Exception as e:
        log.error(f"{connection_id} Error in transcription result wrapper: {e}")

        # Update connection state for error tracking
        connection_state = pipeline_manager.get_connection_state(connection_id)
        if connection_state:
            connection_state.record_failure(f"Result wrapper error: {e}")


async def send_transcription(
    source: FastAPIWebSocketAudioSource,
    transcription: Dict,
    connection_id: str,
    writer: Optional[RecordingWriter] = None,
    wants_events: bool = False,
) -> None:
    """
    Send transcription results to the WebSocket with proper async compatibility and error handling.

    This function handles the transcription result processing for the async pipeline,
    ensuring proper error handling when transcription results are None or invalid,
    and providing comprehensive logging for async operation completion and errors.

    Args:
        source: WebSocket audio source to send the transcription to
        transcription: Transcription data dictionary containing language and transcriptions
        connection_id: ID of the connection for logging and error tracking
        writer: Saves the segments to the user's transcript (None: not saved)
        wants_events: Send the {"type": "transcript"} event when the recording is first saved
    """
    start_time = time.perf_counter()

    try:
        # Validate transcription input
        if transcription is None:
            log.debug(f"{connection_id} Received None transcription, skipping send")
            return

        if not isinstance(transcription, dict):
            log.error(
                f"{connection_id} Invalid transcription type: {type(transcription)}, expected dict"
            )
            return

        # Extract transcription data with proper error handling
        language = transcription.get("language", "")
        transcriptions = transcription.get("transcriptions", [])

        # Validate transcriptions list
        if not isinstance(transcriptions, list):
            log.error(
                f"{connection_id} Invalid transcriptions type: {type(transcriptions)}, expected list"
            )
            return

        # Convert transcription data to the expected WebSocket format
        diarized = []

        for item in transcriptions:
            try:
                # Handle different transcription formats
                if isinstance(item, (tuple, list)) and len(item) >= 4:
                    # Format: (speaker, text, start, end) or [speaker, text, start, end]
                    speaker, text, start, end = item[0], item[1], item[2], item[3]
                elif isinstance(item, dict):
                    # Format: {"speaker": ..., "text": ..., "start": ..., "end": ...}
                    speaker = item.get("speaker", "")
                    text = item.get("text", "")
                    start = item.get("start", 0.0)
                    end = item.get("end", 0.0)
                else:
                    log.warning(
                        f"{connection_id} Invalid transcription item format: {type(item).__name__}"
                    )
                    continue

                # Validate transcription item data
                if not isinstance(text, str) or not text.strip():
                    log.debug(f"{connection_id} Skipping empty or non-text segment ({type(text).__name__})")
                    continue

                # Add to diarized results
                diarized.append(
                    {
                        "language": language,
                        "start": float(start),
                        "end": float(end),
                        "speaker": speaker,  # Keep original type (int or str)
                        "text": str(text).strip(),
                    }
                )

            except Exception as item_error:
                log.error(
                    f"{connection_id} Error processing transcription item {item}: {item_error}"
                )
                continue

        # Send the transcription data
        if diarized:
            try:
                if writer is not None:
                    await _send_and_save(source, diarized, writer, wants_events)
                else:
                    await source.send(json.dumps(diarized))
                instruments.segments.add(len(diarized))

                log.info(
                    f"{connection_id} Successfully sent transcription with {len(diarized)} segments."
                )

                # Update WebSocket session activity
                try:
                    app_config.model_manager.update_websocket_session_activity(
                        connection_id
                    )
                except Exception as activity_error:
                    log.debug(
                        f"{connection_id} Error updating session activity: {activity_error}"
                    )

            except Exception as send_error:
                log.error(
                    f"{connection_id} Failed to send transcription to WebSocket: {send_error}"
                )

                # Update connection state if pipeline manager is available
                connection_state = pipeline_manager.get_connection_state(connection_id)
                if connection_state:
                    connection_state.record_failure(
                        f"WebSocket send error: {send_error}"
                    )

                raise  # Re-raise to allow upstream error handling
        else:
            # Send empty array for empty transcriptions
            try:
                await source.send("[]")
                log.debug(f"{connection_id} Sent empty transcription array")
            except Exception as send_error:
                log.error(
                    f"{connection_id} Failed to send empty transcription: {send_error}"
                )
                raise

    except Exception as e:

        log.error(
            f"{connection_id} Error in send_transciption. Transcription data: {transcription}"
        )

        # Update connection state if pipeline manager is available
        connection_state = pipeline_manager.get_connection_state(connection_id)
        if connection_state:
            connection_state.record_failure(f"send_transcription error: {e}")

        # Don't re-raise the error to avoid crashing the pipeline
        # Log the error and continue processing


async def _send_and_save(
    source: FastAPIWebSocketAudioSource,
    segments: List[Dict],
    writer: RecordingWriter,
    wants_events: bool,
) -> None:
    """Send segments with their ids, then save them. The lock keeps batches in order."""
    async with writer.lock:
        opened = await writer.open()
        if opened and wants_events:
            await source.send(json.dumps(opened))
        writer.stamp(segments)
        await source.send(json.dumps(segments))
        await writer.append(segments)


async def handle_pipeline_error(connection_id: str, error: Exception):
    """
    Handle pipeline errors with connection-specific error isolation and reporting.

    Args:
        connection_id: ID of the connection where error occurred
        error: The error that occurred
    """
    log.error(f"{connection_id} Pipeline error occurred: {error}")

    # Get connection state for error tracking
    connection_state = pipeline_manager.get_connection_state(connection_id)
    if connection_state:
        connection_state.record_failure(f"Pipeline error: {error}")

        # Check if connection should be marked as failed
        if connection_state.error_count >= pipeline_manager.max_error_count:
            log.error(
                f"{connection_id} Connection marked as failed due to excessive errors ({connection_state.error_count})"
            )

            # Optionally, we could close the WebSocket connection here
            # but we'll let the connection continue and let the client decide

    # Log error details for debugging
    log.error(f"{connection_id} Error details: {type(error).__name__}: {str(error)}")

    # Don't re-raise the error to avoid crashing the entire pipeline
    # The pipeline manager will handle error recovery


# need to be after the other handler, otherwise overrides!
import os

if os.path.exists("../webui/dist"):
    app.mount("/", StaticFiles(directory="../webui/dist", html=True))
else:
    log.warning("WebUI dist directory not found, skipping static file mounting")
