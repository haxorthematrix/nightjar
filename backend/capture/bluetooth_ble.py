"""BLE capture via the SENA UD-100 (BlueZ) using `bleak`.

Emits an Observation per advertisement, classifying each device as infotainment / phone /
wearable / unknown from its advertised name, service UUIDs and manufacturer data.

Requires: `pip install bleak`, BlueZ, and the UD-100 plugged in as an hci adapter.
Note: modern phones rotate their BLE address (~15 min) — those are flagged 'resolvable';
persistent fingerprinting focuses on non-rotating infotainment units, matching real TPMS
tracking. Nightjar is receive-only.
"""
from __future__ import annotations

import asyncio

from .base import Observation, Sensor

# manufacturer-data company IDs that indicate a phone/wearable ecosystem
PHONE_COMPANY_IDS = {0x004C: "Apple", 0x0006: "Microsoft", 0x00E0: "Google",
                     0x0075: "Samsung", 0x0087: "Garmin", 0x0157: "Huami"}

INFOTAINMENT_HINTS = ["sync", "uconnect", "mylink", "mbux", "idrive", "carplay",
                      "uvo", "starlink", "audio", "car", "vw", "toyota", "honda",
                      "kenwood", "pioneer", "alpine", "bose"]


class BleSensor(Sensor):
    name = "ble"
    description = "BLE devices via SENA UD-100 (bleak/BlueZ)"

    async def run(self) -> None:
        try:
            from bleak import BleakScanner
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("bleak not installed. Run: pip install bleak") from exc

        cfg = self.config.get("ble", {})
        adapter = cfg.get("adapter", "hci0")
        active = cfg.get("active_scan", True)

        queue: asyncio.Queue = asyncio.Queue()

        def _cb(device, adv):
            try:
                queue.put_nowait((device, adv))
            except asyncio.QueueFull:
                pass

        scanner_kwargs = {"detection_callback": _cb, "scanning_mode": "active" if active else "passive"}
        try:
            scanner = BleakScanner(adapter=adapter, **scanner_kwargs)
        except TypeError:
            scanner = BleakScanner(**scanner_kwargs)

        await scanner.start()
        await self.log(f"BLE scanning on {adapter} ({'active' if active else 'passive'})")
        try:
            while True:
                device, adv = await queue.get()
                await self.emit(self._to_observation(device, adv))
        finally:
            await scanner.stop()

    def _to_observation(self, device, adv) -> Observation:
        name = (getattr(adv, "local_name", None) or getattr(device, "name", None) or "")
        mfg = dict(getattr(adv, "manufacturer_data", {}) or {})
        uuids = list(getattr(adv, "service_uuids", []) or [])
        rssi = getattr(adv, "rssi", getattr(device, "rssi", None))
        addr = device.address

        category = "unknown"
        vendor = None
        for cid in mfg:
            if cid in PHONE_COMPANY_IDS:
                category = "phone"
                vendor = PHONE_COMPANY_IDS[cid]
                break
        if category == "unknown" and any(h in name.lower() for h in INFOTAINMENT_HINTS):
            category = "entertainment"

        # BLE address type — random/resolvable addresses rotate; static ones persist
        addr_type = getattr(device, "address_type", None)
        resolvable = str(addr_type).lower() in ("random", "randomresolvable") or addr[0:1] in "cdef"

        return Observation(
            kind="ble",
            identifier=addr,
            category=category,
            label=name,
            rssi=rssi,
            data={
                "name": name,
                "vendor": vendor,
                "service_uuids": uuids,
                "manufacturer_ids": [hex(c) for c in mfg],
                "resolvable_addr": resolvable,
            },
        )
