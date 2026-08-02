"""Backfill synthetic capture images for detections that don't have one (mock data).

Run with the server stopped:
    PYTHONPATH=. .venv/bin/python scripts/backfill_capture_images.py
"""
from __future__ import annotations

from backend.capture import synthimg
from backend.database import session_scope
from backend.models import Detection, Vehicle
from backend.settings import ROOT, get_settings


def main() -> None:
    if not synthimg.available():
        raise SystemExit("Pillow not installed — run: .venv/bin/pip install Pillow")
    cap_dir = get_settings().captures_dir
    n = 0
    with session_scope() as db:
        dets = db.query(Detection).filter((Detection.image_path == "")
                                          | (Detection.image_path.is_(None))).all()
        for det in dets:
            if not det.plate_text:
                continue
            color = "#38bdf8"
            if det.vehicle_id:
                veh = db.get(Vehicle, det.vehicle_id)
                if veh:
                    color = veh.color
            rel = synthimg.make_capture_file(
                cap_dir, ROOT, det.plate_text, color, det.ts,
                region=det.region or "", confidence=det.plate_confidence)
            if rel:
                det.image_path = rel
                n += 1
    print(f"backfilled {n} capture image(s)")


if __name__ == "__main__":
    main()
