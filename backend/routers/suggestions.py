from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select

from ..database import session_scope
from ..models import Suggestion
from ..serialize import suggestion_dict

router = APIRouter(prefix="/api/suggestions", tags=["suggestions"])


@router.get("")
def list_suggestions(status: str = "pending", limit: int = Query(100, le=1000)):
    with session_scope() as db:
        stmt = select(Suggestion).order_by(Suggestion.confidence.desc(),
                                           Suggestion.created_at.desc())
        if status != "all":
            stmt = stmt.where(Suggestion.status == status)
        rows = db.scalars(stmt.limit(limit)).all()
        return [suggestion_dict(db, s) for s in rows]


@router.post("/{suggestion_id}/accept")
async def accept(suggestion_id: int, request: Request):
    if not _exists(suggestion_id):
        raise HTTPException(404, "not found")
    await request.app.state.ingestor.accept_suggestion(suggestion_id)
    return {"ok": True, "status": "accepted"}


@router.post("/{suggestion_id}/reject")
async def reject(suggestion_id: int, request: Request):
    if not _exists(suggestion_id):
        raise HTTPException(404, "not found")
    await request.app.state.ingestor.reject_suggestion(suggestion_id)
    return {"ok": True, "status": "rejected"}


def _exists(sid: int) -> bool:
    with session_scope() as db:
        return db.get(Suggestion, sid) is not None
