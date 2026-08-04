"""Webcam capture + ALPR.

Trigger modes:
  - interval : grab a frame every N seconds
  - rf_event : grab a frame when a new persistent RF identifier enters the correlation scene
  - manual   : only when POST /api/services/camera/snapshot is called
  - motion   : simple frame-difference motion gate

Each capture is written to data/captures/ and handed to the pipeline as a CameraCapture.
Requires: `pip install opencv-python-headless numpy` and a webcam.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import select

from ..database import session_scope
from ..models import Signal
from ..settings import ROOT, get_settings
from ..timeutil import now as _now
from .alpr import get_alpr
from .base import CameraCapture, Sensor


class CameraSensor(Sensor):
    name = "camera"
    description = "Webcam capture + ALPR plate recovery"

    def __init__(self, ingestor, config):
        super().__init__(ingestor, config)
        self._snapshot_flag = asyncio.Event()
        self._autoadjust_flag = asyncio.Event()

    def request_snapshot(self) -> None:
        self._snapshot_flag.set()

    def request_autoadjust(self) -> None:
        self._autoadjust_flag.set()

    def _apply_auto_profile(self, cap) -> None:
        """Re-optimize the camera: auto-exposure + auto white balance, backlight comp off,
        neutral gain/brightness. Handy after physically re-aiming/shading the camera."""
        import cv2  # type: ignore
        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 3)   # V4L2 UVC: 3=auto (aperture priority), 1=manual
        cap.set(cv2.CAP_PROP_AUTO_WB, 1)
        cap.set(cv2.CAP_PROP_BACKLIGHT, 0)       # backlight compensation off (better outdoors)
        cap.set(cv2.CAP_PROP_GAIN, 0)
        cap.set(cv2.CAP_PROP_BRIGHTNESS, 128)

    def _has_nonbaseline(self, signal_ids) -> bool:
        """True if any of the given signals is NOT baselined (a passing/unknown unit)."""
        ids = list(signal_ids)
        if not ids:
            return False
        with session_scope() as db:
            return db.scalar(select(Signal.id).where(
                Signal.id.in_(ids), Signal.is_baseline.is_(False)).limit(1)) is not None

    async def run(self) -> None:
        try:
            import cv2  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "opencv not installed. Run: pip install opencv-python-headless numpy") from exc

        cfg = self.config.get("camera", {})
        device = cfg.get("device", 0)
        trigger = cfg.get("trigger", "rf_event")
        interval = float(cfg.get("interval", 5))
        min_conf = float(cfg.get("min_plate_confidence", 0.5))
        captures_dir: Path = get_settings().captures_dir

        alpr = get_alpr(cfg.get("alpr_backend", "auto"))
        await self.log(f"ALPR backend: {alpr.backend}")

        # Blocking OpenCV calls run in a thread so they never freeze the server's event loop
        # (a non-streaming device can block cap.read() for many seconds per call).
        cap = await asyncio.to_thread(cv2.VideoCapture, device, cv2.CAP_V4L2)
        if not cap.isOpened():
            cap = await asyncio.to_thread(cv2.VideoCapture, device)  # fallback: any backend
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open camera device {device}")
        try:
            cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 2500)   # fail fast when not streaming
        except Exception:  # noqa: BLE001
            pass

        # Request capture resolution. V4L2 snaps to the nearest the device actually offers,
        # so we log what we truly get (an under-spec'd device / VM virtual camera will cap it).
        want_w, want_h = int(cfg.get("width", 1920)), int(cfg.get("height", 1080))
        fourcc = cfg.get("fourcc")
        try:
            if fourcc:
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*str(fourcc)))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, want_w)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, want_h)
        except Exception:  # noqa: BLE001
            pass
        got_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        got_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.stats["resolution"] = f"{got_w}x{got_h}"
        note = "" if (got_w, got_h) == (want_w, want_h) else f" (requested {want_w}x{want_h})"
        await self.log(f"Camera resolution: {got_w}x{got_h}{note}"
                       + ("" if got_h >= 720 else " — low for ALPR; see USB passthrough note"),
                       level="info" if got_h >= 720 else "warn")

        # Probe: opening succeeds even when no video is actually streaming into the device
        # (e.g. a VM virtual camera with no host passthrough). Confirm frames flow so the
        # operator gets a clear signal instead of silent no-ops. Time-box the read — a
        # non-streaming V4L2 device blocks ~10s per read regardless of READ_TIMEOUT_MSEC.
        streaming = False
        try:
            ok, _f = await asyncio.wait_for(asyncio.to_thread(cap.read), timeout=4.0)
            streaming = bool(ok and _f is not None)
        except asyncio.TimeoutError:
            streaming = False
        self.stats["streaming"] = streaming
        if streaming:
            await self.log(f"Camera online (trigger={trigger}) — frames OK")
        else:
            await self.log("Camera device opened but is returning NO frames — is the webcam "
                           "actually streaming? In a VM, attach/enable the camera passthrough "
                           "(e.g. VMware: VM > Removable Devices > connect the camera).",
                           level="warn")

        prev_scene: set[int] = set()
        prev_gray = None
        try:
            while True:
                if self._autoadjust_flag.is_set():
                    self._autoadjust_flag.clear()
                    await asyncio.to_thread(self._apply_auto_profile, cap)
                    await self.log("Camera auto-adjusted: auto-exposure + auto white balance, "
                                   "backlight compensation off")
                fire = False
                if trigger == "interval":
                    await asyncio.sleep(interval)
                    fire = True
                elif trigger == "manual":
                    await self._snapshot_flag.wait()
                    self._snapshot_flag.clear()
                    fire = True
                elif trigger == "rf_event":
                    await asyncio.sleep(0.5)
                    active = set(self.ingestor.correlation.active_signals(_now()))
                    new = active - prev_scene
                    prev_scene = active
                    # fire only when a NON-baseline unit newly enters the scene (a passing /
                    # unknown vehicle) — not our baselined surroundings re-appearing
                    fire = bool(new) and self._has_nonbaseline(new)
                else:  # motion
                    await asyncio.sleep(0.3)

                if self._snapshot_flag.is_set():
                    self._snapshot_flag.clear()
                    fire = True

                # only grab a frame when we intend to capture (or for motion detection)
                if not fire and trigger != "motion":
                    continue

                ok, frame = await asyncio.to_thread(cap.read)
                if not ok or frame is None:
                    if fire:
                        await self.log("Capture requested but the camera returned no frame "
                                       "(device opened, but not streaming).", level="warn")
                    continue

                if trigger == "motion":
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    gray = cv2.GaussianBlur(gray, (21, 21), 0)
                    if prev_gray is not None:
                        delta = cv2.absdiff(prev_gray, gray)
                        fire = bool((delta > 25).mean() > 0.02)
                    prev_gray = gray

                if not fire:
                    continue

                ts = _now()
                fname = captures_dir / f"cap_{ts.strftime('%Y%m%d_%H%M%S_%f')}.jpg"
                await asyncio.to_thread(cv2.imwrite, str(fname), frame)

                result = await asyncio.to_thread(alpr.read, frame)
                plate = region = None
                conf = None
                bbox = {}
                if result and result.confidence >= min_conf:
                    plate, conf, region = result.text, result.confidence, result.region
                    bbox = result.bbox or {}

                await self.emit_capture(CameraCapture(
                    image_path=str(fname.relative_to(ROOT)),  # router serves ROOT/image_path
                    plate_text=plate, plate_confidence=conf, region=region, bbox=bbox,
                    meta={"trigger": trigger},
                ))
        finally:
            await asyncio.to_thread(cap.release)
