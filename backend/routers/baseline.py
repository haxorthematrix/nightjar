from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from sqlalchemy import select, update

from ..database import session_scope
from ..models import Signal
from ..schemas import BaselineLearn

router = APIRouter(prefix="/api/baseline", tags=["baseline"])


@router.post("/learn")
def learn(body: BaselineLearn):
    """Mark currently-known signals as baseline (your own devices/environment)."""
    with session_scope() as db:
        stmt = update(Signal).values(is_baseline=True)
        if body.within_minutes:
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=body.within_minutes)
            stmt = stmt.where(Signal.last_seen >= cutoff)
        db.execute(stmt)
        count = db.scalar(select(Signal.id).where(Signal.is_baseline.is_(True)).limit(1))
    return {"ok": True, "baselined_all": body.within_minutes is None}


@router.post("/reset")
def reset():
    with session_scope() as db:
        db.execute(update(Signal).values(is_baseline=False))
    return {"ok": True}
