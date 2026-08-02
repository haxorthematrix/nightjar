"""Normalized capture data types and the Sensor plugin interface.

Every capture source (real or simulated) converts its native data into an `Observation`
(an RF identifier sighting) or a `CameraCapture`, and hands it to the shared `Ingestor`.
Sensors never touch the database or the UI directly.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ..timeutil import now as _now

if TYPE_CHECKING:
    from ..ingest import Ingestor


@dataclass
class Observation:
    """A single sighting of an RF identifier."""

    kind: str                       # tpms | ble | bt_classic
    identifier: str                 # protocol:id  or  aa:bb:cc:dd:ee:ff
    rssi: float | None = None
    category: str = "unknown"       # tpms | entertainment | phone | wearable | unknown
    label: str = ""
    source: str = ""                # which sensor produced it
    ts: datetime = field(default_factory=_now)
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class CameraCapture:
    image_path: str
    plate_text: str | None = None
    plate_confidence: float | None = None
    region: str | None = None
    bbox: dict[str, Any] = field(default_factory=dict)
    source: str = "camera"
    ts: datetime = field(default_factory=_now)
    meta: dict[str, Any] = field(default_factory=dict)


class Sensor:
    """Base class for a capture service.

    Subclasses implement `run()` — a coroutine that loops until cancelled, calling
    `self.emit(...)` / `self.emit_capture(...)` for each observation. Lifecycle
    (start/stop/status/error) is handled here.
    """

    name: str = "sensor"
    #: human-facing description shown in the UI
    description: str = ""

    def __init__(self, ingestor: "Ingestor", config: dict[str, Any]):
        self.ingestor = ingestor
        self.config = config
        self._task: asyncio.Task | None = None
        self.status: str = "stopped"     # stopped|starting|running|error
        self.last_error: str = ""
        self.stats: dict[str, Any] = {"emitted": 0, "started_at": None}

    # ---- lifecycle -------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        self.status = "starting"
        self.last_error = ""
        self.stats["started_at"] = _now().isoformat()
        await self._publish_status()
        self._task = asyncio.create_task(self._runner(), name=f"sensor:{self.name}")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        self.status = "stopped"
        await self._publish_status()

    async def _runner(self) -> None:
        try:
            self.status = "running"
            await self._publish_status()
            await self.run()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self.status = "error"
            self.last_error = f"{type(exc).__name__}: {exc}"
            await self.log(self.last_error, level="error")
            await self._publish_status()

    # ---- helpers for subclasses -----------------------------------------
    async def emit(self, obs: Observation) -> None:
        obs.source = obs.source or self.name
        self.stats["emitted"] += 1
        await self.ingestor.observation(obs)

    async def emit_capture(self, cap: CameraCapture) -> None:
        cap.source = cap.source or self.name
        self.stats["emitted"] += 1
        await self.ingestor.capture(cap)

    async def log(self, message: str, level: str = "info") -> None:
        await self.ingestor.bus.publish(
            "log", {"service": self.name, "level": level, "message": message}
        )

    async def _publish_status(self) -> None:
        await self.ingestor.bus.publish("service.status", self.snapshot())

    def snapshot(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "status": self.status,
            "running": self.running,
            "last_error": self.last_error,
            "stats": self.stats,
            "info": self.describe(),
        }

    def describe(self) -> dict[str, Any]:
        """Optional per-sensor config summary shown in the UI (radios, adapter, …)."""
        return {}

    # ---- to be implemented ----------------------------------------------
    async def run(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError
