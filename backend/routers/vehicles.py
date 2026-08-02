from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from ..database import session_scope
from ..models import Sighting, Signal, Vehicle
from ..schemas import VehicleDetach, VehiclePatch, VehicleSplit
from ..serialize import vehicle_dict
from ..vehicle_ops import block_between, pick_color, recompute_or_delete

router = APIRouter(prefix="/api/vehicles", tags=["vehicles"])


@router.get("")
def list_vehicles(
    order: str = Query("score", pattern="^(score|last_seen|first_seen)$"),
    limit: int = Query(200, le=1000),
):
    with session_scope() as db:
        col = getattr(Vehicle, order)
        rows = db.scalars(
            select(Vehicle).where(Vehicle.status == "active")
            .order_by(col.desc()).limit(limit)
        ).all()
        return [vehicle_dict(v) for v in rows]


@router.get("/{vehicle_id}")
def get_vehicle(vehicle_id: int):
    with session_scope() as db:
        v = db.get(Vehicle, vehicle_id)
        if not v:
            raise HTTPException(404, "not found")
        data = vehicle_dict(v, deep=True)
        # build a merged recent timeline across member signals
        sig_ids = [s.id for s in v.signals]
        timeline = []
        if sig_ids:
            rows = db.scalars(
                select(Sighting).where(Sighting.signal_id.in_(sig_ids))
                .order_by(Sighting.ts.desc()).limit(300)
            ).all()
            timeline = [
                {"ts": r.ts.isoformat(), "signal_id": r.signal_id,
                 "rssi": r.rssi, "source": r.source}
                for r in rows
            ]
        data["timeline"] = timeline
        return data


@router.post("/{vehicle_id}/split")
def split_vehicle(vehicle_id: int, body: VehicleSplit):
    """Pull the given member signals out of this vehicle into a NEW vehicle."""
    with session_scope() as db:
        v = db.get(Vehicle, vehicle_id)
        if not v:
            raise HTTPException(404, "not found")
        sel = set(body.signal_ids)
        move = [s for s in v.signals if s.id in sel]
        if not move:
            raise HTTPException(400, "no matching members to split")
        remaining = {s.id for s in v.signals if s.id not in sel}

        newv = Vehicle(label=body.label or "")
        db.add(newv)
        db.flush()
        newv.color = pick_color(newv.id)
        for s in move:
            s.vehicle_id = newv.id
        db.flush()
        if body.block_cross and remaining:
            block_between(db, {s.id for s in move}, remaining)
        recompute_or_delete(db, newv.id)
        src = recompute_or_delete(db, vehicle_id)
        db.flush()
        db.expire_all()   # drop stale relationship collections before serializing
        src = db.get(Vehicle, vehicle_id)
        return {"new": vehicle_dict(db.get(Vehicle, newv.id), deep=True),
                "source": vehicle_dict(src, deep=True) if src else None}


@router.post("/{vehicle_id}/detach")
def detach_from_vehicle(vehicle_id: int, body: VehicleDetach):
    """Detach the given member signals (they become uncorrelated / vehicle_id null)."""
    with session_scope() as db:
        v = db.get(Vehicle, vehicle_id)
        if not v:
            raise HTTPException(404, "not found")
        sel = set(body.signal_ids)
        move = [s for s in v.signals if s.id in sel]
        if not move:
            raise HTTPException(400, "no matching members to detach")
        remaining = {s.id for s in v.signals if s.id not in sel}
        for s in move:
            s.vehicle_id = None
        db.flush()
        if body.block and remaining:
            block_between(db, {s.id for s in move}, remaining)
        detached_ids = [s.id for s in move]
        recompute_or_delete(db, vehicle_id)
        db.flush()
        db.expire_all()
        src = db.get(Vehicle, vehicle_id)
        return {"detached": detached_ids,
                "source": vehicle_dict(src, deep=True) if src else None}


@router.patch("/{vehicle_id}")
def patch_vehicle(vehicle_id: int, body: VehiclePatch):
    with session_scope() as db:
        v = db.get(Vehicle, vehicle_id)
        if not v:
            raise HTTPException(404, "not found")
        if body.label is not None:
            v.label = body.label
        if body.notes is not None:
            v.notes = body.notes
        if body.status is not None:
            v.status = body.status
        db.flush()
        return vehicle_dict(v, deep=True)
