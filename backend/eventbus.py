"""A tiny in-process async pub/sub used to fan capture events out to WebSocket clients.

Also keeps a bounded ring buffer so a freshly-connected UI can replay the last N events
(live feed feels populated immediately).
"""
from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone
from typing import Any


class EventBus:
    def __init__(self, history: int = 200):
        self._subscribers: set[asyncio.Queue] = set()
        self._history: deque[dict] = deque(maxlen=history)
        self._lock = asyncio.Lock()

    async def publish(self, type_: str, payload: Any) -> None:
        event = {
            "type": type_,
            "payload": payload,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        self._history.append(event)
        # copy to avoid mutation during iteration
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # slow consumer — drop rather than block the pipeline
                pass

    def publish_soon(self, type_: str, payload: Any) -> None:
        """Fire-and-forget publish usable from sync code inside the running loop."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self.publish(type_, payload))

    async def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        async with self._lock:
            self._subscribers.add(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue) -> None:
        async with self._lock:
            self._subscribers.discard(q)

    def recent(self) -> list[dict]:
        return list(self._history)


bus = EventBus()
