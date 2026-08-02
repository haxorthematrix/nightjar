"""Simulator sensor — drives the full pipeline with realistic synthetic traffic.

Active by default (mock_mode) so the entire system — ingest, correlation, plate binding,
notifications, and the live UI — is exercisable before any hardware exists. Models a small
population of "vehicles," each owning several TPMS sensor IDs plus BLE infotainment/phone
devices, that periodically drive past (RSSI rises and falls). Some passes trigger a
synthetic plate capture, which binds a plate to the vehicle's RF identity end-to-end.
"""
from __future__ import annotations

import asyncio
import math
import random

from ..settings import ROOT, get_settings
from ..timeutil import now as _now
from . import synthimg
from .base import CameraCapture, Observation, Sensor

VEHICLE_COLORS = ["#38bdf8", "#f472b6", "#a3e635", "#fbbf24", "#c084fc", "#fb7185"]

TPMS_BRANDS = ["Toyota", "Ford", "Schrader", "Continental", "Hyundai", "Honda_PMV"]
INFOTAINMENT_NAMES = ["SYNC 3", "Uconnect", "MyLink", "MBUX", "iDrive", "CarPlay-A1",
                      "Kia UVO", "Subaru STARLINK"]
PHONE_HINTS = ["iPhone", "Pixel", "Galaxy S", "", ""]
PLATE_LETTERS = "ABCDEFGHJKLMNPRSTUVWXYZ"


def _rand_mac(rng: random.Random) -> str:
    return ":".join(f"{rng.randint(0, 255):02X}" for _ in range(6))


def _rand_plate(rng: random.Random) -> str:
    return (f"{''.join(rng.choice(PLATE_LETTERS) for _ in range(3))}"
            f"{rng.randint(1000, 9999)}")


class SimVehicle:
    def __init__(self, rng: random.Random, idx: int):
        self.name = f"sim-{idx}"
        self.tpms = [
            (rng.choice(TPMS_BRANDS), rng.randint(0, 0xFFFFFFFF))
            for _ in range(rng.choice([2, 4, 4]))
        ]
        self.ble = []
        # infotainment head unit (persistent identifier)
        if rng.random() < 0.85:
            self.ble.append(("entertainment", _rand_mac(rng), rng.choice(INFOTAINMENT_NAMES)))
        # occupant phone (may rotate MAC in reality; here stable for demo)
        if rng.random() < 0.7:
            self.ble.append(("phone", _rand_mac(rng), rng.choice(PHONE_HINTS)))
        self.plate = _rand_plate(rng)
        self.region = rng.choice(["us-ca", "us-tx", "us-ny", "us-fl", "us-wa"])
        self.base_rssi = rng.uniform(-70, -55)
        self.color = VEHICLE_COLORS[idx % len(VEHICLE_COLORS)]


class SimulatorSensor(Sensor):
    name = "simulator"
    description = "Synthetic multi-modal traffic generator (mock mode)"

    async def run(self) -> None:
        rng = random.Random(1337)
        # scene window governs how far apart passes must be to count as distinct
        # co-occurrence episodes; keep the inter-pass gap comfortably above it.
        self._scene_window = int(self.config.get("correlation", {}).get("scene_window", 20))
        population = [SimVehicle(rng, i) for i in range(6)]
        await self.log(f"Simulator online — {len(population)} synthetic vehicles")

        first = True
        while True:
            v = rng.choices(population, weights=[3, 3, 2, 2, 1, 1])[0]
            # First encounter always captures a plate so the full de-anonymization chain
            # (RF -> vehicle -> plate -> critical alert) is visible within ~a minute.
            capture = True if first else (rng.random() < 0.4)
            first = False
            await self._encounter(rng, v, passes=rng.choice([3, 3, 4]), capture=capture)

            # background: a lone phone drives by (new-signal alert, no correlation)
            if rng.random() < 0.4:
                await self.emit(Observation(
                    kind="ble", identifier=_rand_mac(rng), category="phone",
                    rssi=round(rng.uniform(-92, -80), 1),
                    data={"name": rng.choice(PHONE_HINTS), "transient": True},
                ))

            await asyncio.sleep(rng.uniform(4, 9))

    async def _encounter(self, rng, v: SimVehicle, passes: int = 3,
                         capture: bool = False) -> None:
        """Simulate the vehicle passing the sensor `passes` times.

        Each pass is a short burst of co-present sightings; the gap between passes exceeds
        the correlation scene window so every pass registers as a fresh co-occurrence
        episode — after `merge_threshold` passes the vehicle's signals union together.
        """
        gap = self._scene_window + 4
        for p in range(passes):
            steps = rng.choice([4, 5, 6])
            for i in range(steps):
                # bell-curve RSSI: weakest at the ends, strongest at closest approach
                frac = i / (steps - 1)
                rssi = v.base_rssi + 12 * math.exp(-((frac - 0.5) * 3) ** 2) - 8
                jitter = rng.uniform(-1.5, 1.5)
                for brand, sid in v.tpms:
                    await self.emit(Observation(
                        kind="tpms", identifier=f"{brand}:0x{sid:08X}", category="tpms",
                        rssi=round(rssi + jitter, 1),
                        data={"model": brand, "id": sid,
                              "pressure_kPa": round(rng.uniform(210, 250), 1),
                              "temperature_C": rng.randint(18, 42)},
                    ))
                for cat, mac, nm in v.ble:
                    await self.emit(Observation(
                        kind="ble", identifier=mac, category=cat, label=nm,
                        rssi=round(rssi + jitter + rng.uniform(-1.5, 1.5), 1),
                        data={"name": nm, "vehicle_sim": v.name},
                    ))
                # near closest approach, grab a plate
                if capture and i == steps // 2 and p == passes - 1:
                    conf = round(rng.uniform(0.72, 0.97), 2)
                    cap_ts = _now()
                    img_rel = synthimg.make_capture_file(
                        get_settings().captures_dir, ROOT, v.plate, v.color, cap_ts,
                        region=v.region, confidence=conf)
                    await self.emit_capture(CameraCapture(
                        image_path=img_rel, plate_text=v.plate, plate_confidence=conf,
                        region=v.region, ts=cap_ts,
                        bbox={"x": 220, "y": 160, "w": 180, "h": 60},
                        meta={"synthetic": True, "vehicle_sim": v.name, "color": v.color},
                    ))
                await asyncio.sleep(rng.uniform(0.6, 1.1))
            if p < passes - 1:
                await asyncio.sleep(gap + rng.uniform(0, 4))
