"""TPMS capture via RTL-SDR + the `rtl_433` system binary.

Supports one or many dongles. Each configured *radio* becomes its own `rtl_433` process,
pinned to a specific device index (`-d`), so with two RTL-SDRs you can cover 315 MHz and
433.92 MHz **simultaneously** (no hopping). A single radio with multiple frequencies
hops (`-H`) across them instead. `-M level` gives per-record RSSI for the correlation
RSSI-profile gate.

Config (tpms:):
  radios:                                  # explicit, one process per dongle
    - {device: 0, frequency: "315M",    gain: "49.6"}
    - {device: 1, frequency: "433.92M", gain: "49.6"}
  # or, single dongle, hop the list:
  frequencies: ["315M", "433.92M"]
  hop_interval: 30

Requires: `apt install rtl-433` (+ `rtl-sdr` for udev rules) and RTL-SDR dongle(s).
"""
from __future__ import annotations

import asyncio
import json
import shutil

from .base import Observation, Sensor


class TpmsSensor(Sensor):
    name = "tpms"
    description = "TPMS sensor IDs via RTL-SDR (rtl_433)"

    def _radios(self, cfg: dict) -> list[dict]:
        """Normalize config into a list of radio specs (one rtl_433 process each)."""
        radios = cfg.get("radios")
        if radios:
            out = []
            for r in radios:
                freqs = r.get("frequencies") or ([r["frequency"]] if r.get("frequency")
                                                 else ["315M"])
                out.append({"device": r.get("device"), "frequencies": list(freqs),
                            "gain": r.get("gain"),
                            "hop_interval": r.get("hop_interval", cfg.get("hop_interval", 30))})
            return out
        # single-dongle fallback: one process hopping the frequency list
        return [{"device": cfg.get("device"),
                 "frequencies": list(cfg.get("frequencies", ["315M", "433.92M"])),
                 "gain": cfg.get("gain"),
                 "hop_interval": cfg.get("hop_interval", 30)}]

    def describe(self) -> dict:
        return {"radios": [{"device": r["device"], "frequencies": r["frequencies"],
                            "gain": r["gain"]} for r in self._radios(self.config.get("tpms", {}))]}

    async def run(self) -> None:
        cfg = self.config.get("tpms", {})
        binary = cfg.get("rtl_433_bin", "rtl_433")
        if shutil.which(binary) is None:
            raise RuntimeError(f"'{binary}' not found. Install with: sudo apt install rtl-433")

        protocols = cfg.get("protocols", [])
        extra = cfg.get("extra_args", [])
        radios = self._radios(cfg)

        for r in radios:
            dev = "auto" if r["device"] is None else f"dev{r['device']}"
            await self.log(f"rtl_433 radio [{dev}] on {', '.join(r['frequencies'])}"
                           + (f" hop {r['hop_interval']}s" if len(r["frequencies"]) > 1 else "")
                           + (f" gain {r['gain']}" if r["gain"] else ""))

        self._alive = {i: True for i in range(len(radios))}
        tasks = [asyncio.create_task(self._pump(i, binary, r, protocols, extra))
                 for i, r in enumerate(radios)]
        try:
            await asyncio.gather(*tasks)
            # gather returns only once every radio process has exited
            raise RuntimeError("all rtl_433 radios exited — check the RTL-SDR dongle(s)")
        finally:
            for t in tasks:
                t.cancel()

    async def _pump(self, idx: int, binary: str, radio: dict, protocols, extra) -> None:
        freqs = radio["frequencies"]
        args = [binary, "-F", "json", "-M", "level", "-M", "time:iso"]
        if radio["device"] is not None:
            args += ["-d", str(radio["device"])]
        for f in freqs:
            args += ["-f", f]
        if len(freqs) > 1:
            args += ["-H", str(radio["hop_interval"])]
        if radio["gain"]:
            args += ["-g", str(radio["gain"])]
        for p in protocols:
            args += ["-R", str(p)]
        args += extra

        try:
            proc = await asyncio.create_subprocess_exec(
                *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        except Exception as exc:  # noqa: BLE001
            await self.log(f"radio[{radio['device']}] failed to launch: {exc}", level="error")
            return
        stderr_task = asyncio.create_task(self._drain_stderr(proc, radio["device"]))
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
            # a device error (e.g. busy/unplugged) ends the stream; log but let siblings live
            await self.log(f"radio[{radio['device']}] rtl_433 exited (code {rc})",
                           level="error" if rc else "info")
        finally:
            stderr_task.cancel()
            if proc.returncode is None:
                proc.terminate()

    async def _drain_stderr(self, proc, device) -> None:
        assert proc.stderr is not None
        async for raw in proc.stderr:
            line = raw.decode(errors="replace").strip()
            low = line.lower()
            if line and any(k in low for k in ("error", "usb", "no supported", "fail",
                                               "tuned to", "tuner", "pll not")):
                await self.log(f"rtl_433[dev{device}]: {line}", level="info")

    def _to_observation(self, rec: dict, freqs) -> Observation | None:
        is_tpms = rec.get("type") == "TPMS" or "TPMS" in str(rec.get("model", ""))
        if not is_tpms:
            return None
        model = str(rec.get("model", "TPMS"))
        sid = rec.get("id", rec.get("sensor_id", "?"))
        data = {k: v for k, v in rec.items() if k != "time"}
        data.setdefault("freq", rec.get("freq", ",".join(freqs)))
        return Observation(kind="tpms", identifier=f"{model}:{sid}", category="tpms",
                           rssi=rec.get("rssi"), label=model, data=data)
