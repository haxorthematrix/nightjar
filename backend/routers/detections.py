from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select

from ..database import session_scope
from ..models import Detection
from ..serialize import detection_dict
from ..settings import ROOT

router = APIRouter(prefix="/api/detections", tags=["detections"])


@router.get("")
def list_detections(
    with_plate: bool | None = None,
    limit: int = Query(200, le=1000),
):
    with session_scope() as db:
        stmt = select(Detection).order_by(Detection.ts.desc())
        if with_plate is True:
            stmt = stmt.where(Detection.plate_text.isnot(None))
        elif with_plate is False:
            stmt = stmt.where(Detection.plate_text.is_(None))
        rows = db.scalars(stmt.limit(limit)).all()
        return [detection_dict(d) for d in rows]


@router.get("/{detection_id}/image")
def image(detection_id: int):
    with session_scope() as db:
        d = db.get(Detection, detection_id)
        if not d or not d.image_path:
            raise HTTPException(404, "no image")
        path = (ROOT / d.image_path).resolve()
        # confine to project dir
        if not str(path).startswith(str(ROOT)) or not path.exists():
            raise HTTPException(404, "image missing")
        return FileResponse(path)
