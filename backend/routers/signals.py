from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import or_, select

from ..database import session_scope
from ..models import Association, Signal, Sighting, Vehicle
from ..schemas import SignalPatch, SignalReassign
from ..serialize import association_dict, signal_dict
from ..vehicle_ops import block_between, recompute_or_delete

router = APIRouter(prefix="/api/signals", tags=["signals"])


@router.get("")
def list_signals(
    kind: str | None = None,
    category: str | None = None,
    q: str | None = None,
    baseline: bool | None = None,
    order: str = Query("last_seen", pattern="^(last_seen|first_seen|count|identifier)$"),
    limit: int = Query(200, le=1000),
    offset: int = 0,
):
    with session_scope() as db:
        stmt = select(Signal)
        if kind:
            stmt = stmt.where(Signal.kind == kind)
        if category:
            stmt = stmt.where(Signal.category == category)
        if baseline is not None:
            stmt = stmt.where(Signal.is_baseline.is_(baseline))
        if q:
            like = f"%{q}%"
            stmt = stmt.where(or_(Signal.identifier.ilike(like), Signal.label.ilike(like)))
        col = getattr(Signal, order)
        stmt = stmt.order_by(col.desc() if order != "identifier" else col.asc())
        stmt = stmt.limit(limit).offset(offset)
        rows = db.scalars(stmt).all()
        return [signal_dict(s) for s in rows]


@router.get("/{signal_id}")
def get_signal(signal_id: int):
    with session_scope() as db:
        s = db.get(Signal, signal_id)
        if not s:
            raise HTTPException(404, "not found")
        return signal_dict(s)


@router.patch("/{signal_id}")
def patch_signal(signal_id: int, body: SignalPatch):
    with session_scope() as db:
        s = db.get(Signal, signal_id)
        if not s:
            raise HTTPException(404, "not found")
        if body.label is not None:
            s.label = body.label
        if body.is_baseline is not None:
            s.is_baseline = body.is_baseline
        if body.notes is not None:
            s.notes = body.notes
        if body.category is not None:
            s.category = body.category
        db.flush()
        return signal_dict(s)


@router.post("/{signal_id}/reassign")
def reassign_signal(signal_id: int, body: SignalReassign):
    """Move a signal to another vehicle (vehicle_id), or detach it (vehicle_id null)."""
    with session_scope() as db:
        s = db.get(Signal, signal_id)
        if not s:
            raise HTTPException(404, "not found")
        if body.vehicle_id is not None and db.get(Vehicle, body.vehicle_id) is None:
            raise HTTPException(400, "target vehicle not found")
        old = s.vehicle_id
        s.vehicle_id = body.vehicle_id
        db.flush()
        if body.block and old and old != body.vehicle_id:
            members = {x.id for x in db.scalars(
                select(Signal).where(Signal.vehicle_id == old)).all()}
            block_between(db, {signal_id}, members)
        recompute_or_delete(db, old)
        if body.vehicle_id is not None:
            recompute_or_delete(db, body.vehicle_id)
        db.flush()
        return signal_dict(s)


@router.get("/{signal_id}/associations")
def associations(signal_id: int, limit: int = Query(50, le=500)):
    """Units this unit has been co-present with, strongest first — its correlation graph."""
    with session_scope() as db:
        rows = db.scalars(
            select(Association)
            .where(or_(Association.a_id == signal_id, Association.b_id == signal_id))
            .order_by(Association.co_count.desc()).limit(limit)
        ).all()
        return [association_dict(db, a, from_id=signal_id) for a in rows]


@router.get("/{signal_id}/sightings")
def sightings(signal_id: int, limit: int = Query(500, le=5000)):
    with session_scope() as db:
        rows = db.scalars(
            select(Sighting).where(Sighting.signal_id == signal_id)
            .order_by(Sighting.ts.desc()).limit(limit)
        ).all()
        return [
            {"id": r.id, "ts": r.ts.isoformat(), "rssi": r.rssi,
             "source": r.source, "data": r.data}
            for r in rows
        ]
