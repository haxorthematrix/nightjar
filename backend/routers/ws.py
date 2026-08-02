from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..eventbus import bus

router = APIRouter()


@router.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    q = await bus.subscribe()
    try:
        # replay recent history so a fresh client isn't blank
        await websocket.send_json({"type": "hello", "payload": {"history": bus.recent()}})
        while True:
            event = await q.get()
            await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        pass
    finally:
        await bus.unsubscribe(q)
