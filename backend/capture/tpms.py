"""TPMS capture via RTL-SDR + the `rtl_433` system binary.

A single RTL-SDR can only be tuned to one frequency at a time, so with multiple bands we run
ONE `rtl_433` process that frequency-hops (its `-H` option) across them — not one process per
band (which would fail to claim the shared device). Parses the JSON line stream and emits an
Observation for every TPMS record. `-M level` gives per-record RSSI (needed by the
correlation RSSI-profile gate).

Requires: `apt install rtl-433` (+ `rtl-sdr` for udev rules) and an RTL-SDR dongle.
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
        hop = int(cfg.get("hop_interval", 30))
        protocols = cfg.get("protocols", [])
        extra = cfg.get("extra_args", [])

        args = [binary, "-F", "json", "-M", "level", "-M", "time:iso"]
        for f in freqs:
            args += ["-f", f]
        if len(freqs) > 1:
            args += ["-H", str(hop)]      # hop across the listed frequencies
        for p in protocols:
            args += ["-R", str(p)]
        args += extra

        hop_note = f", hopping every {hop}s" if len(freqs) > 1 else ""
        await self.log(f"rtl_433 listening on {', '.join(freqs)}{hop_note}")

        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stderr_task = asyncio.create_task(self._drain_stderr(proc))
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
                obs = self._to_observation(rec, freqs)
                if obs:
                    await self.emit(obs)
            rc = await proc.wait()
            if rc != 0:
                raise RuntimeError(f"rtl_433 exited (code {rc}) — check the RTL-SDR dongle")
        finally:
            stderr_task.cancel()
            if proc.returncode is None:
                proc.terminate()

    async def _drain_stderr(self, proc) -> None:
        """Surface rtl_433's device/tuning errors as service logs (helps live bring-up)."""
        assert proc.stderr is not None
        async for raw in proc.stderr:
            line = raw.decode(errors="replace").strip()
            low = line.lower()
            if line and any(k in low for k in ("error", "usb", "no supported", "fail",
                                               "tuned to", "tuner")):
                await self.log(f"rtl_433: {line}", level="info")

    def _to_observation(self, rec: dict, freqs) -> Observation | None:
        is_tpms = rec.get("type") == "TPMS" or "TPMS" in str(rec.get("model", ""))
        if not is_tpms:
            return None
        model = str(rec.get("model", "TPMS"))
        sid = rec.get("id", rec.get("sensor_id", "?"))
        data = {k: v for k, v in rec.items() if k != "time"}
        data.setdefault("freq", rec.get("freq", ",".join(freqs)))
        return Observation(
            kind="tpms",
            identifier=f"{model}:{sid}",
            category="tpms",
            rssi=rec.get("rssi"),
            label=model,
            data=data,
        )
