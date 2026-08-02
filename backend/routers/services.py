from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/services", tags=["services"])


def _manager(request: Request):
    return request.app.state.manager


@router.get("")
def list_services(request: Request):
    return _manager(request).list()


@router.post("/{name}/start")
async def start(name: str, request: Request):
    try:
        return await _manager(request).start(name)
    except KeyError:
        raise HTTPException(404, f"no such service '{name}'")


@router.post("/{name}/stop")
async def stop(name: str, request: Request):
    try:
        return await _manager(request).stop(name)
    except KeyError:
        raise HTTPException(404, f"no such service '{name}'")


@router.post("/camera/snapshot")
def snapshot(request: Request):
    ok = _manager(request).request_snapshot("camera")
    if not ok:
        raise HTTPException(400, "camera service not available")
    return {"queued": True}
