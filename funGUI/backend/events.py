from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class GuiEvent:
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def to_sse(self) -> str:
        body = {"type": self.type, "payload": self.payload, "created_at": self.created_at}
        return f"event: {self.type}\ndata: {json.dumps(body, ensure_ascii=False)}\n\n"


class EventBus:
    def __init__(self) -> None:
        self._clients: dict[str, asyncio.Queue[GuiEvent]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, client_id: str) -> asyncio.Queue[GuiEvent]:
        queue: asyncio.Queue[GuiEvent] = asyncio.Queue(maxsize=500)
        async with self._lock:
            self._clients[client_id] = queue
        await queue.put(GuiEvent("connected", {"client_id": client_id}))
        return queue

    async def disconnect(self, client_id: str) -> None:
        async with self._lock:
            self._clients.pop(client_id, None)

    async def publish(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        event = GuiEvent(event_type, payload or {})
        async with self._lock:
            clients = list(self._clients.values())
        for queue in clients:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            await queue.put(event)
