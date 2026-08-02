from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from ..database import session_scope
from ..models import Sighting, Signal, Vehicle
from ..schemas import VehiclePatch
from ..serialize import vehicle_dict

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
