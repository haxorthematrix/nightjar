"""The single ingest pipeline. All sensors feed here.

observation() / capture() = persist -> correlate (accumulate association evidence, maybe
raise a suggestion) -> apply notification rules -> publish events. Correlation NEVER
auto-merges; vehicles form only when the operator accepts a suggestion (accept_suggestion).
Nothing else in the system writes sighting/signal/detection rows.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .capture.base import CameraCapture, Observation
from .correlation import CorrelationEngine
from .database import session_scope
from .models import Signal, Sighting
from .notifications import NotificationEngine
from .serialize import detection_dict, signal_dict
from .timeutil import iso


class Ingestor:
    def __init__(self, bus, correlation: CorrelationEngine, notifier: NotificationEngine):
        self.bus = bus
        self.correlation = correlation
        self.notifier = notifier

    async def _dispatch(self, events: list[dict[str, Any]]) -> None:
        """Publish correlation events and fire the notification rules tied to them."""
        for ev in events:
            t = ev["type"]
            if t == "plate_bound":
                p = ev["payload"]
                await self.notifier.on_plate_bound(p["vehicle"], p["detection_id"], p["plate_text"])
                continue
            await self.bus.publish(t, ev["payload"])
            if t == "vehicle.new":
                await self.notifier.on_vehicle(ev["payload"], created=True)
            elif t == "suggestion.new":
                await self.notifier.on_suggestion(ev["payload"])

    # -------------------------------------------------------------- signals
    async def observation(self, obs: Observation) -> None:
        # Upsert with one retry: select-then-insert isn't atomic, so a concurrent first
        # sighting of the same identifier can lose the race on INSERT (UNIQUE constraint).
        # On IntegrityError we retry; the row now exists, so we take the update path.
        signal_id = is_new = sig_payload = None
        for attempt in range(2):
            try:
                with session_scope() as db:
                    sig = db.scalar(
                        select(Signal).where(Signal.kind == obs.kind,
                                             Signal.identifier == obs.identifier)
                    )
                    is_new = sig is None
                    if is_new:
                        sig = Signal(
                            kind=obs.kind, identifier=obs.identifier,
                            label=obs.label, category=obs.category,
                            first_seen=obs.ts, last_seen=obs.ts, count=1,
                            rssi_last=obs.rssi, rssi_best=obs.rssi,
                            meta=dict(obs.data or {}),
                        )
                        db.add(sig)
                        db.flush()
                    else:
                        sig.last_seen = obs.ts
                        sig.count += 1
                        sig.rssi_last = obs.rssi
                        if obs.rssi is not None:
                            sig.rssi_best = obs.rssi if sig.rssi_best is None else max(sig.rssi_best, obs.rssi)
                        if obs.category != "unknown" and sig.category == "unknown":
                            sig.category = obs.category
                        if obs.label and not sig.label:
                            sig.label = obs.label
                        if obs.data:
                            merged = dict(sig.meta or {})
                            merged.update(obs.data)
                            sig.meta = merged

                    db.add(Sighting(signal_id=sig.id, ts=obs.ts, rssi=obs.rssi,
                                    source=obs.source, data=dict(obs.data or {})))
                    db.flush()
                    signal_id = sig.id
                    sig_payload = signal_dict(sig)
                break
            except IntegrityError:
                if attempt == 0:
                    continue   # lost the insert race — retry as an update
                raise

        await self.bus.publish("sighting.new", {
            "signal_id": signal_id, "kind": sig_payload["kind"],
            "identifier": sig_payload["identifier"], "category": sig_payload["category"],
            "rssi": obs.rssi, "ts": iso(obs.ts), "source": obs.source,
            "count": sig_payload["count"], "is_new": is_new,
        })
        await self.bus.publish("signal.new" if is_new else "signal.update", sig_payload)

        await self.notifier.on_signal(sig_payload, is_new)
        await self._dispatch(self.correlation.observe(signal_id, obs.ts, obs.rssi))

    # ------------------------------------------------------------ detections
    async def capture(self, cap: CameraCapture) -> None:
        with session_scope() as db:
            from .models import Detection
            det = Detection(
                ts=cap.ts, image_path=cap.image_path, plate_text=cap.plate_text,
                plate_confidence=cap.plate_confidence, region=cap.region,
                bbox=dict(cap.bbox or {}), meta=dict(cap.meta or {}),
            )
            db.add(det)
            db.flush()
            det_id = det.id
            det_payload = detection_dict(det)

        await self.bus.publish("detection.new", det_payload)
        await self._dispatch(self.correlation.bind_detection(det_id, cap.ts))

    # ------------------------------------------------------- operator actions
    async def accept_suggestion(self, suggestion_id: int) -> None:
        await self._dispatch(self.correlation.accept_suggestion(suggestion_id))

    async def reject_suggestion(self, suggestion_id: int) -> None:
        await self._dispatch(self.correlation.reject_suggestion(suggestion_id))
