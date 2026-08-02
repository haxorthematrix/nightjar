"""Capture manager — owns sensor instances and their lifecycle.

In mock mode the only service is the Simulator (it stands in for every modality). With
hardware attached (mock_mode: false) it builds the real TPMS / BLE / classic-BT / camera
sensors according to the `services` config.
"""
from __future__ import annotations

from typing import Any

from ..database import session_scope
from ..models import ServiceState
from .base import Sensor
from .bluetooth_ble import BleSensor
from .camera import CameraSensor
from .mock import SimulatorSensor
from .tpms import TpmsSensor


class CaptureManager:
    def __init__(self, settings, ingestor):
        self.settings = settings
        self.ingestor = ingestor
        self.sensors: dict[str, Sensor] = {}
        self._build()

    def _build(self) -> None:
        cfg = self.settings.data
        svc = cfg.get("services", {})
        mock = cfg.get("mock_mode", True)

        def enabled(name: str) -> bool:
            return svc.get(name, {}).get("enabled", False)

        if mock:
            if enabled("simulator"):
                self.sensors["simulator"] = SimulatorSensor(self.ingestor, cfg)
        else:
            if enabled("tpms"):
                self.sensors["tpms"] = TpmsSensor(self.ingestor, cfg)
            if enabled("ble"):
                self.sensors["ble"] = BleSensor(self.ingestor, cfg)
            if enabled("bt_classic"):
                # optional classic-BT inquiry sensor; reuse BLE module's classifier later
                pass
            if enabled("camera"):
                self.sensors["camera"] = CameraSensor(self.ingestor, cfg)

        # persist initial state rows
        with session_scope() as db:
            for name in self.sensors:
                if db.get(ServiceState, name) is None:
                    db.add(ServiceState(name=name, status="stopped", enabled=True))

    # ------------------------------------------------------------- control
    async def autostart(self) -> None:
        svc = self.settings.data.get("services", {})
        for name, sensor in self.sensors.items():
            if svc.get(name, {}).get("autostart", False):
                await self.start(name)

    async def start(self, name: str) -> dict[str, Any]:
        sensor = self._require(name)
        await sensor.start()
        self._persist(sensor)
        return sensor.snapshot()

    async def stop(self, name: str) -> dict[str, Any]:
        sensor = self._require(name)
        await sensor.stop()
        self._persist(sensor)
        return sensor.snapshot()

    async def restart(self, name: str) -> dict[str, Any]:
        sensor = self._require(name)
        await sensor.stop()
        await sensor.start()
        self._persist(sensor)
        return sensor.snapshot()

    async def stop_all(self) -> None:
        for sensor in self.sensors.values():
            await sensor.stop()

    def snapshot(self, name: str) -> dict[str, Any]:
        return self._require(name).snapshot()

    def list(self) -> list[dict[str, Any]]:
        return [s.snapshot() for s in self.sensors.values()]

    def request_snapshot(self, name: str = "camera") -> bool:
        sensor = self.sensors.get(name)
        if isinstance(sensor, CameraSensor):
            sensor.request_snapshot()
            return True
        return False

    # ------------------------------------------------------------- helpers
    def _require(self, name: str) -> Sensor:
        if name not in self.sensors:
            raise KeyError(name)
        return self.sensors[name]

    def _persist(self, sensor: Sensor) -> None:
        with session_scope() as db:
            row = db.get(ServiceState, sensor.name) or ServiceState(name=sensor.name)
            row.status = sensor.status
            row.last_error = sensor.last_error
            row.stats = sensor.stats
            db.add(row)
