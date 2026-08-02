from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select, update

from ..database import session_scope
from ..models import Notification
from ..serialize import notification_dict

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("")
def list_notifications(
    level: str | None = None,
    acknowledged: bool | None = None,
    limit: int = Query(100, le=1000),
):
    with session_scope() as db:
        stmt = select(Notification).order_by(Notification.ts.desc())
        if level:
            stmt = stmt.where(Notification.level == level)
        if acknowledged is not None:
            stmt = stmt.where(Notification.acknowledged.is_(acknowledged))
        rows = db.scalars(stmt.limit(limit)).all()
        return [notification_dict(n) for n in rows]


@router.post("/{notification_id}/ack")
def ack(notification_id: int):
    with session_scope() as db:
        n = db.get(Notification, notification_id)
        if not n:
            raise HTTPException(404, "not found")
        n.acknowledged = True
        db.flush()
        return notification_dict(n)


@router.post("/ack-all")
def ack_all():
    with session_scope() as db:
        db.execute(update(Notification).where(Notification.acknowledged.is_(False))
                   .values(acknowledged=True))
    return {"ok": True}
