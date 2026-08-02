from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from .. import __version__
from ..database import session_scope
from ..models import Detection, Notification, Signal, Sighting, Suggestion, Vehicle
from ..settings import get_settings

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/status")
def status(request: Request):
    settings = get_settings()
    day_ago = datetime.now(timezone.utc) - timedelta(hours=24)
    with session_scope() as db:
        signals = db.scalar(select(func.count(Signal.id)))
        baselined = db.scalar(select(func.count(Signal.id)).where(Signal.is_baseline.is_(True)))
        vehicles = db.scalar(select(func.count(Vehicle.id)))
        detections = db.scalar(select(func.count(Detection.id)))
        plates = db.scalar(select(func.count(Detection.id)).where(Detection.plate_text.isnot(None)))
        sightings = db.scalar(select(func.count(Sighting.id)))
        unacked = db.scalar(
            select(func.count(Notification.id)).where(Notification.acknowledged.is_(False)))
        pending_sug = db.scalar(
            select(func.count(Suggestion.id)).where(Suggestion.status == "pending"))
        new_today = db.scalar(select(func.count(Signal.id)).where(Signal.first_seen >= day_ago))
        by_cat = dict(db.execute(
            select(Signal.category, func.count(Signal.id)).group_by(Signal.category)).all())

    manager = request.app.state.manager
    return {
        "version": __version__,
        "mock_mode": settings["mock_mode"],
        "counts": {
            "signals": signals or 0,
            "baselined": baselined or 0,
            "vehicles": vehicles or 0,
            "detections": detections or 0,
            "plates": plates or 0,
            "sightings": sightings or 0,
            "unacked_notifications": unacked or 0,
            "pending_suggestions": pending_sug or 0,
            "new_signals_24h": new_today or 0,
        },
        "signals_by_category": by_cat,
        "services": manager.list(),
    }
