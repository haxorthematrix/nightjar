"""Shared helpers for manual vehicle editing (split / detach / reassign).

Used by the vehicles and signals routers so an operator can correct the correlator's
mistakes: pull sensors out of a wrongly-merged vehicle, detach a mis-attached device, or
move a unit to the right vehicle. Optionally blocks the associations between the separated
groups so the engine won't immediately re-suggest merging them back.
"""
from __future__ import annotations

from sqlalchemy import func, or_, select

from .correlation import PALETTE, CorrelationEngine
from .models import Association, Detection, Notification, Signal, Vehicle


def recompute_or_delete(db, vehicle_id: int | None) -> Vehicle | None:
    """Recompute a vehicle's aggregates, or delete it if it has no members left.

    Uses fresh COUNT queries (not the ORM relationship collection, which can be stale after
    a child's FK is reassigned directly within the same session)."""
    if vehicle_id is None:
        return None
    v = db.get(Vehicle, vehicle_id)
    if v is None:
        return None
    n_sig = db.scalar(select(func.count(Signal.id)).where(Signal.vehicle_id == v.id))
    n_det = db.scalar(select(func.count(Detection.id)).where(Detection.vehicle_id == v.id))
    if not n_sig and not n_det:
        for n in db.scalars(select(Notification).where(Notification.vehicle_id == v.id)).all():
            n.vehicle_id = None
        db.delete(v)
        return None
    CorrelationEngine._recompute(db, v)
    return v


def block_between(db, group_a: set[int], group_b: set[int]) -> int:
    """Mark associations that cross the two id groups as blocked (no re-suggestion)."""
    a, b = set(group_a), set(group_b)
    if not a or not b:
        return 0
    ids = a | b
    n = 0
    for assoc in db.scalars(
        select(Association).where(or_(Association.a_id.in_(ids), Association.b_id.in_(ids)))
    ).all():
        x, y = assoc.a_id, assoc.b_id
        if not assoc.blocked and ((x in a and y in b) or (x in b and y in a)):
            assoc.blocked = True
            n += 1
    return n


def pick_color(seed: int) -> str:
    return PALETTE[seed % len(PALETTE)]
