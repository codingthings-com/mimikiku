import base64
import logging
from pathlib import Path
from typing import Any, AnyStr, Awaitable, Callable, Dict, Optional, Text

import numpy as np
from fastapi import WebSocket, WebSocketDisconnect
from fastapi.websockets import WebSocketState
from rx.subject import Subject

log = logging.getLogger(__name__)


class FastAPIWebSocketAudioSource:
    """
    Represents an audio source using FastAPI's WebSocket.
    Decoded audio messages are pushed into an RxPY Subject (self.stream).
    Audio is dropped until set_ready() is called. `tap`, if given, is awaited
    with every chunk that enters the pipeline (used to store recording audio).
    """

    def __init__(
        self,
        sample_rate: int,
        websocket: WebSocket,
        tap: Optional[Callable[[np.ndarray], Awaitable[None]]] = None,
    ):
        self.uri = "fastapi_ws"
        self.sample_rate = sample_rate
        self.websocket: WebSocket = websocket
        self.stream: Subject = Subject()
        self.client: Optional[Dict[Text, Any]] = None
        self._ready = False
        self._dropped_chunks = 0
        self.samples_received = 0  # after set_ready(), i.e. what was transcribed
        self.tap = tap
    
    def set_ready(self):
        """Signal that the pipeline is ready to receive audio."""
        if self._dropped_chunks > 0:
            log.info(f"Pipeline ready - dropped {self._dropped_chunks} early chunks")
        self._ready = True

    def decode_audio(self, data: Text) -> np.ndarray:
        byte_samples = base64.decodebytes(data.encode("utf-8"))
        samples = np.frombuffer(byte_samples, dtype=np.float32)
        if len(samples) == 0:
            return np.array([])
        
        # Debug: log audio stats periodically
        if hasattr(self, '_decode_count'):
            self._decode_count += 1
        else:
            self._decode_count = 1
        
        if self._decode_count % 50 == 1:  # Log every 50th chunk
            rms = float(np.sqrt(np.mean(np.square(samples))))
            max_val = float(np.max(np.abs(samples)))
            log.debug(f"[AUDIO_STATS] chunk={self._decode_count}, samples={len(samples)}, "
                    f"RMS={rms:.6f}, max={max_val:.6f}, dtype={samples.dtype}")
        
        return samples.reshape(1, -1)

    async def read(self):
        """
        Continuously reads messages from the websocket.
        Each message is decoded and pushed to the Rx stream.
        Audio is dropped until set_ready() is called.
        """
        chunk_count = 0
        try:
            while True:
                message = await self.websocket.receive_text()
                audio = self.decode_audio(message)
                chunk_count += 1
                
                # Drop audio until pipeline is ready
                if not self._ready:
                    self._dropped_chunks += 1
                    continue
                
                samples = audio.shape[1] if len(audio.shape) > 1 else len(audio)
                duration_ms = (samples / self.sample_rate) * 1000
                log.debug(f"[AUDIO_IN] Chunk #{chunk_count}: {samples} samples ({duration_ms:.1f}ms)")
                self.stream.on_next(audio)
                self.samples_received += samples
                if self.tap is not None:
                    await self.tap(audio)
        except WebSocketDisconnect:
            log.info(f"WebSocket disconnected. Total chunks received: {chunk_count}, dropped: {self._dropped_chunks}")
            self.stream.on_completed()
        except Exception as e:
            log.error(f"Error reading from WebSocket: {e}")
            self.stream.on_error(e)

    async def send(self, message: AnyStr):
        """
        Send a message back to the client via the websocket.
        If the websocket is closed, the message will not be sent.
        """
        if message:
            try:
                if self.websocket.client_state == WebSocketState.CONNECTED:
                    await self.websocket.send_text(message)
                else:
                    log.warning("WebSocket is not connected. Cannot send message.")
                    self.close()
            except Exception as e:
                log.error(f"Error sending message over WebSocket: {e}")
                self.close()

    def close(self):
        """
        Complete the Rx stream. The websocket will be closed by the FastAPI framework.
        """
        log.info("Closing WebSocket and completing stream.")
        self.stream.on_completed()
