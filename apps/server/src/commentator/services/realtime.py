"""Thread-safe fan-out of state messages to WebSocket clients."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Literal

MessageType = Literal["game", "recognition", "commentary", "speech"]

@dataclass(eq=False)
class RealtimeClient:
    """One connection's outbound queue, owned by the connection's event loop."""

    loop: asyncio.AbstractEventLoop
    max_pending: int
    queue: asyncio.Queue[dict[str, Any] | None] = field(default_factory=asyncio.Queue)
    closed: bool = False

    def deliver(self, message: dict[str, Any]) -> None:
        """Schedule a message from any thread without blocking the caller."""
        if self.closed:
            return
        try:
            self.loop.call_soon_threadsafe(self._enqueue, message)
        except RuntimeError:
            # The connection's loop has already stopped.
            self.closed = True

    def _enqueue(self, message: dict[str, Any]) -> None:
        if self.closed:
            return
        if self.queue.qsize() >= self.max_pending:
            # A slow client is disconnected instead of delaying everyone else.
            # On reconnect it receives a fresh snapshot, so no state is lost.
            self.closed = True
            while not self.queue.empty():
                self.queue.get_nowait()
            self.queue.put_nowait(None)
            logging.getLogger("commentator.realtime").warning(
                "realtime client too slow",
                extra={"event": "realtime_client_overflow"},
            )
            return
        self.queue.put_nowait(message)


class RealtimeHub:
    """Assign one global sequence and deliver messages in that order.

    ``publish`` only enqueues work on each client's loop, so it may be called while
    holding the game or recognition lock. The hub lock is always the innermost lock.
    """

    def __init__(self, *, max_pending: int = 64) -> None:
        if max_pending < 1:
            raise ValueError("未送信メッセージの上限は1以上にしてください")
        self.max_pending = max_pending
        self._lock = Lock()
        self._sequence = 0
        self._clients: set[RealtimeClient] = set()

    @property
    def client_count(self) -> int:
        with self._lock:
            return len(self._clients)

    def connect(self) -> RealtimeClient:
        """Register a client for the calling coroutine's running loop."""
        client = RealtimeClient(asyncio.get_running_loop(), self.max_pending)
        with self._lock:
            self._clients.add(client)
        return client

    def disconnect(self, client: RealtimeClient) -> None:
        client.closed = True
        with self._lock:
            self._clients.discard(client)

    def publish(self, message_type: MessageType, payload: dict[str, Any] | None) -> None:
        with self._lock:
            message = self._message(message_type, payload)
            for client in tuple(self._clients):
                client.deliver(message)

    def send_to(
        self,
        client: RealtimeClient,
        message_type: MessageType,
        payload: dict[str, Any] | None,
    ) -> None:
        with self._lock:
            client.deliver(self._message(message_type, payload))

    def _message(
        self, message_type: MessageType, payload: dict[str, Any] | None
    ) -> dict[str, Any]:
        self._sequence += 1
        return {"type": message_type, "sequence": self._sequence, "payload": payload}
