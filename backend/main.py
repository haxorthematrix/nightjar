from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import __version__
from .capture.manager import CaptureManager
from .correlation import CorrelationEngine
from .database import init_db
from .eventbus import bus
from .ingest import Ingestor
from .notifications import NotificationEngine
from .routers import (
    baseline,
    detections,
    notifications,
    services,
    signals,
    suggestions,
    system,
    vehicles,
    ws,
)
from .settings import ROOT, get_settings

log = logging.getLogger("nightjar")
FRONTEND = ROOT / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    init_db()

    corr_cfg = settings["correlation"]
    correlation = CorrelationEngine(
        scene_window=corr_cfg["scene_window"],
        min_encounters=corr_cfg["min_encounters"],
        anchor_categories=corr_cfg.get("anchor_categories"),
        durability=corr_cfg.get("durability"),
        min_rssi_corr=corr_cfg.get("min_rssi_corr", 0.5),
        min_rssi_samples=corr_cfg.get("min_rssi_samples", 6),
        pair_window=corr_cfg.get("pair_window", 6),
    )
    notifier = NotificationEngine(bus, allowlist=settings["baseline"].get("allowlist", []))
    ingestor = Ingestor(bus, correlation, notifier)
    manager = CaptureManager(settings, ingestor)

    app.state.settings = settings
    app.state.manager = manager
    app.state.ingestor = ingestor

    await manager.autostart()
    log.info("Nightjar %s ready (mock_mode=%s)", __version__, settings["mock_mode"])
    try:
        yield
    finally:
        await manager.stop_all()


def create_app() -> FastAPI:
    app = FastAPI(title="Nightjar", version=__version__, lifespan=lifespan)

    for r in (system, services, signals, vehicles, detections, notifications,
              suggestions, baseline):
        app.include_router(r.router)
    app.include_router(ws.router)

    if FRONTEND.exists():
        app.mount("/", StaticFiles(directory=str(FRONTEND), html=True), name="frontend")
    return app


app = create_app()
