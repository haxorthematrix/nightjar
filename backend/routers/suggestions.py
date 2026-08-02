from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select

from ..database import session_scope
from ..eventbus import bus
from ..models import Association, Suggestion
from ..serialize import suggestion_dict
from ..timeutil import now as _now

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


@router.post("/dismiss-all")
async def dismiss_all(block: bool = False):
    """Reject every pending suggestion (clear the backlog). With block=true also blocks the
    associations so those exact pairs won't be re-proposed."""
    with session_scope() as db:
        pend = db.scalars(select(Suggestion).where(Suggestion.status == "pending")).all()
        count = len(pend)
        for s in pend:
            s.status = "rejected"
            s.resolved_at = _now()
            if block:
                a_id, b_id = min(s.a_id, s.b_id), max(s.a_id, s.b_id)
                assoc = db.scalar(select(Association).where(
                    Association.a_id == a_id, Association.b_id == b_id))
                if assoc:
                    assoc.blocked = True
    await bus.publish("suggestion.resolved", {"dismissed": count})
    return {"ok": True, "dismissed": count, "blocked": block}


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
