"""TPMS capture via RTL-SDR + the `rtl_433` system binary.

Runs one `rtl_433 -F json` subprocess per configured frequency (315 MHz and 433.92 MHz by
default) so both common TPMS bands are covered at once, parses the JSON line stream, and
emits an Observation for every TPMS record.

Requires: `apt install rtl-433` and an RTL-SDR dongle. No python package needed.
"""
from __future__ import annotations

import asyncio
import json
import shutil

from .base import Observation, Sensor


class TpmsSensor(Sensor):
    name = "tpms"
    description = "TPMS sensor IDs via RTL-SDR (rtl_433)"

    async def run(self) -> None:
        cfg = self.config.get("tpms", {})
        binary = cfg.get("rtl_433_bin", "rtl_433")
        if shutil.which(binary) is None:
            raise RuntimeError(
                f"'{binary}' not found. Install with: sudo apt install rtl-433")

        freqs = cfg.get("frequencies", ["315M", "433.92M"])
        protocols = cfg.get("protocols", [])
        extra = cfg.get("extra_args", [])

        procs = [asyncio.create_task(self._pump(binary, f, protocols, extra))
                 for f in freqs]
        await self.log(f"rtl_433 listening on {', '.join(freqs)}")
        try:
            await asyncio.gather(*procs)
        finally:
            for p in procs:
                p.cancel()

    async def _pump(self, binary, freq, protocols, extra) -> None:
        args = [binary, "-f", freq, "-F", "json", "-M", "level", "-M", "time:iso"]
        for p in protocols:
            args += ["-R", str(p)]
        args += extra
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            assert proc.stdout is not None
            async for raw in proc.stdout:
                line = raw.decode(errors="replace").strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                obs = self._to_observation(rec, freq)
                if obs:
                    await self.emit(obs)
        finally:
            if proc.returncode is None:
                proc.terminate()

    def _to_observation(self, rec: dict, freq: str) -> Observation | None:
        is_tpms = rec.get("type") == "TPMS" or "TPMS" in str(rec.get("model", ""))
        if not is_tpms:
            return None
        model = str(rec.get("model", "TPMS"))
        sid = rec.get("id", rec.get("sensor_id", "?"))
        data = {k: v for k, v in rec.items() if k not in ("time",)}
        data["freq"] = freq
        return Observation(
            kind="tpms",
            identifier=f"{model}:{sid}",
            category="tpms",
            rssi=rec.get("rssi"),
            label=model,
            data=data,
        )
