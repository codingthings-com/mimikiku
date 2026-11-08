"""
WebSocket session tracker for preventing model unloading during active sessions.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Set, Optional
from dataclasses import dataclass
from enum import Enum

from .constants import (
    WEBSOCKET_SESSION_PROTECTION_ENABLED,
    WEBSOCKET_SESSION_GRACE_PERIOD,
    WEBSOCKET_SESSION_CHECK_INTERVAL,
    ALLOW_UNLOAD_DURING_WEBSOCKET_SESSION,
    CONSECUTIVE_PRESSURE_CYCLES_THRESHOLD,
    PRESSURE_CYCLE_DURATION
)

log = logging.getLogger(__name__)


class SessionState(Enum):
    """States for WebSocket sessions."""
    ACTIVE = "active"
    DISCONNECTED = "disconnected"
    GRACE_PERIOD = "grace_period"
    EXPIRED = "expired"


@dataclass
class WebSocketSession:
    """Information about a WebSocket session."""
    connection_id: str
    model_ids: Set[str]
    state: SessionState
    created_at: datetime
    last_activity: datetime
    disconnected_at: Optional[datetime] = None
    
    def update_activity(self):
        """Update the last activity timestamp."""
        self.last_activity = datetime.now()
    
    def disconnect(self):
        """Mark the session as disconnected."""
        self.state = SessionState.DISCONNECTED
        self.disconnected_at = datetime.now()
    
    def is_in_grace_period(self) -> bool:
        """Check if the session is in grace period after disconnect."""
        if self.state != SessionState.DISCONNECTED or not self.disconnected_at:
            return False
        
        grace_end = self.disconnected_at + timedelta(seconds=WEBSOCKET_SESSION_GRACE_PERIOD)
        return datetime.now() < grace_end
    
    def should_expire(self) -> bool:
        """Check if the session should be expired."""
        if self.state == SessionState.ACTIVE:
            return False
        
        if self.state == SessionState.DISCONNECTED and self.disconnected_at:
            grace_end = self.disconnected_at + timedelta(seconds=WEBSOCKET_SESSION_GRACE_PERIOD)
            return datetime.now() >= grace_end
        
        return False


@dataclass
class PressureCycle:
    """Information about a memory pressure cycle."""
    started_at: datetime
    model_id: str
    cycle_count: int = 1
    
    def should_allow_unload(self) -> bool:
        """Check if model should be allowed to unload after consecutive pressure cycles."""
        if self.cycle_count >= CONSECUTIVE_PRESSURE_CYCLES_THRESHOLD:
            return True
        
        # Check if enough time has passed for this cycle
        cycle_duration = datetime.now() - self.started_at
        return cycle_duration.total_seconds() >= PRESSURE_CYCLE_DURATION


class WebSocketSessionTracker:
    """
    Tracks active WebSocket sessions to prevent model unloading during active connections.
    
    This class provides protection against unloading models that are actively being used
    by WebSocket connections, with configurable grace periods and pressure-based overrides.
    """
    
    def __init__(self):
        """Initialize the WebSocket session tracker."""
        self._sessions: Dict[str, WebSocketSession] = {}
        self._model_to_sessions: Dict[str, Set[str]] = {}  # model_id -> set of connection_ids
        self._pressure_cycles: Dict[str, PressureCycle] = {}  # model_id -> pressure cycle info
        self._cleanup_task: Optional[asyncio.Task] = None
        self._running = False
        
        log.info(f"WebSocket session tracker initialized (protection_enabled={WEBSOCKET_SESSION_PROTECTION_ENABLED})")
    
    async def start(self):
        """Start the session tracker and cleanup task."""
        if self._running:
            return
        
        self._running = True
        if WEBSOCKET_SESSION_PROTECTION_ENABLED:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        
        log.info("WebSocket session tracker started")
    
    async def stop(self):
        """Stop the session tracker and cleanup."""
        self._running = False
        
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        
        # Clear all sessions
        self._sessions.clear()
        self._model_to_sessions.clear()
        self._pressure_cycles.clear()
        
        log.info("WebSocket session tracker stopped")
    
    def register_session(self, connection_id: str, model_ids: Set[str]):
        """
        Register a new WebSocket session with associated models.
        
        Args:
            connection_id: ID of the WebSocket connection
            model_ids: Set of model IDs used by this session
        """
        if not WEBSOCKET_SESSION_PROTECTION_ENABLED:
            return
        
        session = WebSocketSession(
            connection_id=connection_id,
            model_ids=model_ids.copy(),
            state=SessionState.ACTIVE,
            created_at=datetime.now(),
            last_activity=datetime.now()
        )
        
        self._sessions[connection_id] = session
        
        # Update model to sessions mapping
        for model_id in model_ids:
            if model_id not in self._model_to_sessions:
                self._model_to_sessions[model_id] = set()
            self._model_to_sessions[model_id].add(connection_id)
        
        log.info(f"Registered WebSocket session {connection_id} with models: {model_ids}")
    
    def update_session_activity(self, connection_id: str):
        """
        Update the activity timestamp for a session.
        
        Args:
            connection_id: ID of the WebSocket connection
        """
        if not WEBSOCKET_SESSION_PROTECTION_ENABLED:
            return
        
        session = self._sessions.get(connection_id)
        if session and session.state == SessionState.ACTIVE:
            session.update_activity()
    
    def disconnect_session(self, connection_id: str):
        """
        Mark a WebSocket session as disconnected.
        
        Args:
            connection_id: ID of the WebSocket connection
        """
        if not WEBSOCKET_SESSION_PROTECTION_ENABLED:
            return
        
        session = self._sessions.get(connection_id)
        if session:
            session.disconnect()
            log.info(f"WebSocket session {connection_id} disconnected, entering grace period")
    
    def can_unload_model(self, model_id: str, is_pressure_situation: bool = False) -> bool:
        """
        Check if a model can be unloaded considering active WebSocket sessions.
        
        Args:
            model_id: ID of the model to check
            is_pressure_situation: Whether this is due to memory pressure
            
        Returns:
            True if model can be unloaded, False otherwise
        """
        if not WEBSOCKET_SESSION_PROTECTION_ENABLED:
            return True
        
        if ALLOW_UNLOAD_DURING_WEBSOCKET_SESSION:
            return True
        
        # Check if model has any active or grace period sessions
        connection_ids = self._model_to_sessions.get(model_id, set())
        
        active_sessions = []
        grace_period_sessions = []
        
        for connection_id in connection_ids:
            session = self._sessions.get(connection_id)
            if not session:
                continue
            
            if session.state == SessionState.ACTIVE:
                active_sessions.append(connection_id)
            elif session.state == SessionState.DISCONNECTED and session.is_in_grace_period():
                grace_period_sessions.append(connection_id)
        
        # If there are active sessions, never allow unload
        if active_sessions:
            log.debug(f"Model {model_id} cannot be unloaded - active WebSocket sessions: {active_sessions}")
            return False
        
        # If there are grace period sessions, check pressure situation
        if grace_period_sessions:
            if is_pressure_situation:
                # Handle consecutive pressure cycles
                return self._handle_pressure_cycle(model_id)
            else:
                log.debug(f"Model {model_id} cannot be unloaded - sessions in grace period: {grace_period_sessions}")
                return False
        
        # No active or grace period sessions, safe to unload
        return True
    
    def get_protected_models(self) -> Set[str]:
        """
        Get the set of models that are currently protected from unloading.
        
        Returns:
            Set of model IDs that should not be unloaded
        """
        if not WEBSOCKET_SESSION_PROTECTION_ENABLED:
            return set()
        
        protected_models = set()
        
        for model_id, connection_ids in self._model_to_sessions.items():
            for connection_id in connection_ids:
                session = self._sessions.get(connection_id)
                if not session:
                    continue
                
                if (session.state == SessionState.ACTIVE or 
                    (session.state == SessionState.DISCONNECTED and session.is_in_grace_period())):
                    protected_models.add(model_id)
                    break
        
        return protected_models
    
    def get_session_stats(self) -> Dict:
        """
        Get statistics about WebSocket sessions.
        
        Returns:
            Dictionary containing session statistics
        """
        stats = {
            "protection_enabled": WEBSOCKET_SESSION_PROTECTION_ENABLED,
            "total_sessions": len(self._sessions),
            "active_sessions": 0,
            "disconnected_sessions": 0,
            "grace_period_sessions": 0,
            "protected_models": len(self.get_protected_models()),
            "pressure_cycles": len(self._pressure_cycles),
            "sessions": {}
        }
        
        for connection_id, session in self._sessions.items():
            if session.state == SessionState.ACTIVE:
                stats["active_sessions"] += 1
            elif session.state == SessionState.DISCONNECTED:
                if session.is_in_grace_period():
                    stats["grace_period_sessions"] += 1
                else:
                    stats["disconnected_sessions"] += 1
            
            stats["sessions"][connection_id] = {
                "state": session.state.value,
                "model_count": len(session.model_ids),
                "created_at": session.created_at.isoformat(),
                "last_activity": session.last_activity.isoformat(),
                "disconnected_at": session.disconnected_at.isoformat() if session.disconnected_at else None,
                "in_grace_period": session.is_in_grace_period()
            }
        
        return stats
    
    def _handle_pressure_cycle(self, model_id: str) -> bool:
        """
        Handle consecutive pressure cycles for a model.
        
        Args:
            model_id: ID of the model under pressure
            
        Returns:
            True if model can be unloaded due to pressure, False otherwise
        """
        current_time = datetime.now()
        
        if model_id in self._pressure_cycles:
            pressure_cycle = self._pressure_cycles[model_id]
            
            # Check if this is a new pressure cycle (enough time has passed)
            time_since_start = current_time - pressure_cycle.started_at
            if time_since_start.total_seconds() >= PRESSURE_CYCLE_DURATION:
                # Start a new cycle
                pressure_cycle.started_at = current_time
                pressure_cycle.cycle_count += 1
            
            # Check if we should allow unload
            if pressure_cycle.should_allow_unload():
                log.warning(f"Allowing model {model_id} unload due to {pressure_cycle.cycle_count} consecutive pressure cycles")
                # Remove the pressure cycle tracking
                del self._pressure_cycles[model_id]
                return True
            else:
                log.debug(f"Model {model_id} pressure cycle {pressure_cycle.cycle_count}, not allowing unload yet")
                return False
        else:
            # Start tracking pressure cycles for this model
            self._pressure_cycles[model_id] = PressureCycle(
                started_at=current_time,
                model_id=model_id
            )
            log.debug(f"Started pressure cycle tracking for model {model_id}")
            return False
    
    async def _cleanup_loop(self):
        """Background cleanup loop for expired sessions."""
        while self._running:
            try:
                await self._cleanup_expired_sessions()
            except Exception as e:
                log.error(f"Error in session cleanup loop: {e}")
            
            try:
                await asyncio.sleep(WEBSOCKET_SESSION_CHECK_INTERVAL)
            except asyncio.CancelledError:
                break
        
        log.debug("Session cleanup loop stopped")
    
    async def _cleanup_expired_sessions(self):
        """Clean up expired sessions and update mappings."""
        expired_sessions = []
        
        for connection_id, session in self._sessions.items():
            if session.should_expire():
                expired_sessions.append(connection_id)
        
        for connection_id in expired_sessions:
            session = self._sessions[connection_id]
            
            # Remove from model mappings
            for model_id in session.model_ids:
                if model_id in self._model_to_sessions:
                    self._model_to_sessions[model_id].discard(connection_id)
                    if not self._model_to_sessions[model_id]:
                        del self._model_to_sessions[model_id]
            
            # Remove session
            del self._sessions[connection_id]
            
            log.debug(f"Cleaned up expired session {connection_id}")
        
        if expired_sessions:
            log.info(f"Cleaned up {len(expired_sessions)} expired WebSocket sessions")