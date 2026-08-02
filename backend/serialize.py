"""Plain dict serializers for ORM rows (used by both REST responses and WS events)."""
from __future__ import annotations

from typing import Any

from .models import Association, Detection, Notification, Signal, Suggestion, Vehicle
from .timeutil import iso as _iso


def signal_dict(s: Signal, include_meta: bool = True) -> dict[str, Any]:
    d = {
        "id": s.id,
        "kind": s.kind,
        "identifier": s.identifier,
        "label": s.label,
        "category": s.category,
        "first_seen": _iso(s.first_seen),
        "last_seen": _iso(s.last_seen),
        "count": s.count,
        "rssi_last": s.rssi_last,
        "rssi_best": s.rssi_best,
        "is_baseline": s.is_baseline,
        "vehicle_id": s.vehicle_id,
        "notes": s.notes,
    }
    if include_meta:
        d["meta"] = s.meta or {}
    return d


def detection_dict(d: Detection) -> dict[str, Any]:
    return {
        "id": d.id,
        "ts": _iso(d.ts),
        "image_path": d.image_path,
        "image_url": f"/api/detections/{d.id}/image" if d.image_path else None,
        "plate_text": d.plate_text,
        "plate_confidence": d.plate_confidence,
        "region": d.region,
        "bbox": d.bbox or {},
        "vehicle_id": d.vehicle_id,
        "meta": d.meta or {},
    }


def vehicle_dict(v: Vehicle, deep: bool = False) -> dict[str, Any]:
    d = {
        "id": v.id,
        "label": v.label or f"Vehicle #{v.id}",
        "first_seen": _iso(v.first_seen),
        "last_seen": _iso(v.last_seen),
        "score": round(v.score, 1),
        "color": v.color,
        "status": v.status,
        "notes": v.notes,
        "signal_count": len(v.signals),
        "detection_count": len(v.detections),
    }
    if deep:
        d["signals"] = [signal_dict(s, include_meta=False) for s in v.signals]
        d["detections"] = [detection_dict(x) for x in v.detections]
    else:
        # lightweight category tally for cards
        cats: dict[str, int] = {}
        for s in v.signals:
            cats[s.category] = cats.get(s.category, 0) + 1
        d["categories"] = cats
        d["has_plate"] = any(x.plate_text for x in v.detections)
    return d


def _unit_ref(s: Signal | None) -> dict[str, Any] | None:
    if s is None:
        return None
    return {"id": s.id, "kind": s.kind, "identifier": s.identifier,
            "category": s.category, "label": s.label, "count": s.count,
            "vehicle_id": s.vehicle_id}


def suggestion_dict(db, s: Suggestion) -> dict[str, Any]:
    a = db.get(Signal, s.a_id)
    b = db.get(Signal, s.b_id) if s.b_id != s.a_id else None
    det = db.get(Detection, s.detection_id) if s.detection_id else None
    return {
        "id": s.id,
        "kind": s.kind,
        "status": s.status,
        "confidence": s.confidence,
        "encounters": s.encounters,
        "rssi_corr": s.rssi_corr,
        "rationale": s.rationale,
        "created_at": _iso(s.created_at),
        "resolved_at": _iso(s.resolved_at),
        "a": _unit_ref(a),
        "b": _unit_ref(b),
        "detection": detection_dict(det) if det else None,
    }


def association_dict(db, assoc: Association, from_id: int | None = None) -> dict[str, Any]:
    other_id = assoc.b_id if from_id == assoc.a_id else assoc.a_id
    other = db.get(Signal, other_id)
    return {
        "id": assoc.id,
        "co_count": assoc.co_count,
        "first_seen": _iso(assoc.first_seen),
        "last_seen": _iso(assoc.last_seen),
        "blocked": assoc.blocked,
        "rssi_corr": assoc.rssi_corr,
        "rssi_samples": assoc.n_rssi,
        "other": _unit_ref(other),
    }


def notification_dict(n: Notification) -> dict[str, Any]:
    return {
        "id": n.id,
        "ts": _iso(n.ts),
        "level": n.level,
        "rule": n.rule,
        "title": n.title,
        "body": n.body,
        "signal_id": n.signal_id,
        "vehicle_id": n.vehicle_id,
        "detection_id": n.detection_id,
        "acknowledged": n.acknowledged,
    }
