"""Correlation engine — persistent, human-in-the-loop unit correlation.

Model (see specification.md §5):
  * The atomic **unit** of identity is one `Signal` — a single TPMS sensor id, or a single
    BT/BLE address. Units are never pre-grouped.
  * As units are co-present in a scene across *separate encounters*, the engine accumulates
    persistent **Association** evidence (co_count) that survives restarts — this is how
    identity is correlated *over time*.
  * When evidence for an anchor pair crosses `min_encounters`, the engine raises a
    **Suggestion**. It NEVER auto-merges — the operator accepts/rejects. Accepting unions the
    units into a `Vehicle`; rejecting blocks the pair from re-suggesting.
  * Vehicle identity anchors on durable identifiers (TPMS + infotainment). Rotating phone
    addresses are weak (low durability) and do not seed vehicles, though their evidence is
    still recorded and they can be attached to a confirmed vehicle.

Only the short-lived "scene" (who is co-present right now) is in memory; all evidence is in
the database.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from .database import session_scope
from .models import Association, Detection, Signal, Suggestion, Vehicle
from .serialize import suggestion_dict, vehicle_dict
from .timeutil import now as _now

PALETTE = [
    "#38bdf8", "#f472b6", "#a3e635", "#fbbf24", "#c084fc",
    "#fb7185", "#34d399", "#60a5fa", "#f59e0b", "#2dd4bf",
]


class CorrelationEngine:
    def __init__(self, scene_window: int = 20, min_encounters: int = 3,
                 anchor_categories: list[str] | None = None,
                 durability: dict[str, float] | None = None,
                 min_rssi_corr: float = 0.5, min_rssi_samples: int = 6,
                 pair_window: int = 6, min_rssi_std: float = 3.0,
                 attach_require_comovement: bool = True):
        self.scene_window = scene_window
        self.min_encounters = min_encounters
        self.anchors = set(anchor_categories or ["tpms", "entertainment"])
        self.durability = durability or {
            "tpms": 1.0, "entertainment": 1.0, "phone": 0.2, "wearable": 0.2, "unknown": 0.5}
        self.min_rssi_corr = min_rssi_corr
        self.min_rssi_samples = min_rssi_samples
        self.pair_window = pair_window
        self.min_rssi_std = min_rssi_std
        self.attach_require_comovement = attach_require_comovement
        # signal_id -> (last ts in scene, last rssi)
        self._scene: dict[int, tuple[datetime, float | None]] = {}
        self._color_i = 0

    # ------------------------------------------------------------ scene mgmt
    def _prune(self, now: datetime) -> None:
        cutoff = now - timedelta(seconds=self.scene_window)
        for sid in [s for s, (t, _) in self._scene.items() if t < cutoff]:
            del self._scene[sid]

    def active_signals(self, now: datetime | None = None) -> list[int]:
        if now:
            self._prune(now)
        return list(self._scene.keys())

    def _dur(self, cat: str) -> float:
        return self.durability.get(cat, 0.5)

    def _is_anchor(self, cat: str) -> bool:
        return cat in self.anchors

    # ------------------------------------------------------------- observe
    def observe(self, signal_id: int, ts: datetime, rssi: float | None = None
                ) -> list[dict[str, Any]]:
        """Register a sighting. Strengthen associations with co-present units — counting a
        co-occurrence episode on (re)entry, and folding contemporaneous RSSI readings into
        an online Pearson correlation of the two units' signal-strength profiles. Never
        merges. Returns events to publish."""
        self._prune(ts)
        events: list[dict[str, Any]] = []
        was_active = signal_id in self._scene
        others = [(sid, t, r) for sid, (t, r) in self._scene.items() if sid != signal_id]
        self._scene[signal_id] = (ts, rssi)

        if not others:
            return events

        with session_scope() as db:
            for oid, ot, orr in others:
                events += self._register_pair(db, signal_id, oid, ts, rssi, ot, orr,
                                              fresh_entry=not was_active)
        return events

    def _register_pair(self, db, sid: int, oid: int, ts: datetime, rssi: float | None,
                       ot: datetime, orr: float | None, fresh_entry: bool
                       ) -> list[dict[str, Any]]:
        a_id, b_id = (sid, oid) if sid < oid else (oid, sid)
        assoc = db.scalar(select(Association).where(
            Association.a_id == a_id, Association.b_id == b_id))
        if assoc is None:
            assoc = Association(a_id=a_id, b_id=b_id, co_count=0, first_seen=ts, last_seen=ts,
                                n_rssi=0, s_a=0.0, s_b=0.0, s_aa=0.0, s_bb=0.0, s_ab=0.0)
            db.add(assoc)
            db.flush()   # materialize column defaults before we accumulate into them

        # a co-occurrence episode = this unit (re)entering the scene while the other is present
        if fresh_entry:
            assoc.co_count += 1
            assoc.last_seen = ts

        # RSSI-profile: pair contemporaneous readings and update the online correlation
        if (rssi is not None and orr is not None
                and abs((ts - ot).total_seconds()) <= self.pair_window):
            self._accumulate_rssi(assoc, sid, rssi, orr)

        db.flush()
        if assoc.blocked:
            return []
        return self._maybe_suggest(db, assoc)

    # ---------------------------------------------------------- rssi profile
    def _accumulate_rssi(self, assoc: Association, sid: int,
                         rssi_self: float, rssi_other: float) -> None:
        # map self/other onto the canonical a/b slots
        if sid == assoc.a_id:
            av, bv = rssi_self, rssi_other
        else:
            av, bv = rssi_other, rssi_self
        assoc.n_rssi += 1
        assoc.s_a += av
        assoc.s_b += bv
        assoc.s_aa += av * av
        assoc.s_bb += bv * bv
        assoc.s_ab += av * bv
        assoc.rssi_corr = self._corr(assoc)

    def _corr(self, assoc: Association) -> float | None:
        n = assoc.n_rssi
        if n < self.min_rssi_samples:
            return None
        num = n * assoc.s_ab - assoc.s_a * assoc.s_b
        va = n * assoc.s_aa - assoc.s_a * assoc.s_a
        vb = n * assoc.s_bb - assoc.s_b * assoc.s_b
        if va <= 1e-9 or vb <= 1e-9:
            return None  # a unit's RSSI is flat -> profile uninformative
        return max(-1.0, min(1.0, num / math.sqrt(va * vb)))

    def _std(self, assoc: Association) -> tuple[float, float]:
        """Standard deviation (dB) of each unit's RSSI in the paired samples. A stationary
        device sits near-flat (small std); a passing one swings as it approaches/departs."""
        n = assoc.n_rssi
        if n < 2:
            return (0.0, 0.0)
        va = max(0.0, assoc.s_aa / n - (assoc.s_a / n) ** 2)
        vb = max(0.0, assoc.s_bb / n - (assoc.s_b / n) ** 2)
        return (math.sqrt(va), math.sqrt(vb))

    # ---------------------------------------------------------- suggestions
    def _maybe_suggest(self, db, assoc: Association) -> list[dict[str, Any]]:
        if assoc.co_count < self.min_encounters:
            return []
        sa = db.get(Signal, assoc.a_id)
        sb = db.get(Signal, assoc.b_id)
        if not sa or not sb:
            return []
        # already the same confirmed vehicle -> nothing to do
        if sa.vehicle_id and sa.vehicle_id == sb.vehicle_id:
            return []

        anchor_a, anchor_b = self._is_anchor(sa.category), self._is_anchor(sb.category)
        # Determine suggestion type; anchors seed vehicles, weak units only *attach*.
        if anchor_a and anchor_b:
            kind = "merge" if (sa.vehicle_id and sb.vehicle_id) else \
                   ("attach" if (sa.vehicle_id or sb.vehicle_id) else "form")
        elif (anchor_a and sa.vehicle_id) or (anchor_b and sb.vehicle_id):
            # weak unit (e.g. phone) frequently seen with a unit already in a vehicle
            kind = "attach"
        else:
            # weak/weak or anchorless pair with no vehicle context — record evidence only
            return []

        # --- RSSI-profile gate --------------------------------------------
        corr = assoc.rssi_corr if assoc.n_rssi >= self.min_rssi_samples else None
        involves_weak = not (anchor_a and anchor_b)

        if kind == "attach" and involves_weak and self.attach_require_comovement:
            # A non-anchor device (phone/wearable/unknown) may only ATTACH to a vehicle if it
            # demonstrably CO-MOVES with it: enough contemporaneous samples, positive RSSI
            # correlation, AND real dynamic range on both units. A stationary neighbour device
            # co-present with a parked vehicle has near-flat RSSI and is rejected here — this
            # is what kills the stationary-BLE attach noise.
            std_a, std_b = self._std(assoc)
            if (corr is None or corr < self.min_rssi_corr
                    or std_a < self.min_rssi_std or std_b < self.min_rssi_std):
                return []
        elif corr is not None and corr < self.min_rssi_corr:
            # anchor pairs (e.g. TPMS wheels): co-occurrence is enough; only block on
            # actively-disagreeing RSSI profiles.
            return []

        pair_dur = min(self._dur(sa.category), self._dur(sb.category))
        enc_factor = min(1.0, assoc.co_count / (2 * self.min_encounters))
        corr_factor = max(0.0, corr) if corr is not None else 0.4  # neutral prior when unknown
        confidence = min(0.99, round(0.4 * enc_factor + 0.6 * corr_factor, 3))
        span_h = max(0.0, (assoc.last_seen - assoc.first_seen).total_seconds() / 3600.0)
        prof = (f"RSSI-profile agreement {corr:+.2f} ({assoc.n_rssi} samples)"
                if corr is not None else f"RSSI profile gathering ({assoc.n_rssi} samples)")
        rationale = (f"{sa.kind}:{sa.identifier} & {sb.kind}:{sb.identifier} co-present across "
                     f"{assoc.co_count} encounters over {span_h:.1f}h; {prof}")

        existing = db.scalar(select(Suggestion).where(
            Suggestion.a_id == assoc.a_id, Suggestion.b_id == assoc.b_id,
            Suggestion.status == "pending"))
        if existing:
            existing.kind = kind
            existing.confidence = confidence
            existing.encounters = assoc.co_count
            existing.rssi_corr = corr
            existing.rationale = rationale
            db.flush()
            return [{"type": "suggestion.update", "payload": suggestion_dict(db, existing)}]

        sug = Suggestion(a_id=assoc.a_id, b_id=assoc.b_id, kind=kind, confidence=confidence,
                         encounters=assoc.co_count, rssi_corr=corr, rationale=rationale,
                         status="pending")
        db.add(sug)
        db.flush()
        return [{"type": "suggestion.new", "payload": suggestion_dict(db, sug)}]

    # ------------------------------------------------------- accept / reject
    def accept_suggestion(self, suggestion_id: int) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        with session_scope() as db:
            sug = db.get(Suggestion, suggestion_id)
            if not sug or sug.status != "pending":
                return events
            events += self._union(db, [sug.a_id, sug.b_id])
            if sug.detection_id:
                events += self._attach_detection(db, sug.detection_id, [sug.a_id, sug.b_id])
            sug.status = "accepted"
            sug.resolved_at = _now()
            db.flush()
            events.append({"type": "suggestion.resolved",
                           "payload": {"id": sug.id, "status": "accepted"}})
        return events

    def reject_suggestion(self, suggestion_id: int) -> list[dict[str, Any]]:
        with session_scope() as db:
            sug = db.get(Suggestion, suggestion_id)
            if not sug or sug.status != "pending":
                return []
            sug.status = "rejected"
            sug.resolved_at = _now()
            a_id, b_id = min(sug.a_id, sug.b_id), max(sug.a_id, sug.b_id)
            assoc = db.scalar(select(Association).where(
                Association.a_id == a_id, Association.b_id == b_id))
            if assoc:
                assoc.blocked = True   # never re-suggest this pair
            db.flush()
            return [{"type": "suggestion.resolved",
                     "payload": {"id": sug.id, "status": "rejected"}}]

    # ------------------------------------------------------------ detection
    def bind_detection(self, detection_id: int, ts: datetime) -> list[dict[str, Any]]:
        """A camera capture. If the active anchor units already belong to a confirmed
        vehicle, bind the plate. Otherwise raise a suggestion (with the detection) so the
        operator can confirm forming a vehicle and binding the plate."""
        self._prune(ts)
        active = list(self._scene.keys())
        events: list[dict[str, Any]] = []
        if not active:
            return events
        with session_scope() as db:
            anchors = [sid for sid in active
                       if (s := db.get(Signal, sid)) and self._is_anchor(s.category)]
            if not anchors:
                return events
            # is there already a confirmed vehicle among the active anchors?
            vid = next((s.vehicle_id for sid in anchors
                        if (s := db.get(Signal, sid)) and s.vehicle_id), None)
            if vid is not None:
                events += self._attach_detection(db, detection_id, anchors)
            else:
                events += self._suggest_from_detection(db, detection_id, anchors, ts)
        return events

    def _suggest_from_detection(self, db, detection_id: int, anchors: list[int],
                                ts: datetime) -> list[dict[str, Any]]:
        det = db.get(Detection, detection_id)
        a_id = min(anchors)
        b_id = max(anchors) if len(anchors) > 1 else a_id
        existing = db.scalar(select(Suggestion).where(
            Suggestion.detection_id == detection_id, Suggestion.status == "pending"))
        if existing:
            return []
        plate = det.plate_text if det else None
        rationale = (f"Plate {plate or '(none)'} captured with {len(anchors)} anchor "
                     f"unit(s) in scene — confirm to form a vehicle and bind the plate.")
        sug = Suggestion(a_id=a_id, b_id=b_id, detection_id=detection_id, kind="form",
                         confidence=round((det.plate_confidence or 0.6), 3) if det else 0.6,
                         encounters=1, rationale=rationale, status="pending")
        db.add(sug)
        db.flush()
        return [{"type": "suggestion.new", "payload": suggestion_dict(db, sug)}]

    def _attach_detection(self, db, detection_id: int, signal_ids: list[int]
                          ) -> list[dict[str, Any]]:
        det = db.get(Detection, detection_id)
        if det is None:
            return []
        vid = next((s.vehicle_id for sid in signal_ids
                    if (s := db.get(Signal, sid)) and s.vehicle_id), None)
        if vid is None:
            return []
        det.vehicle_id = vid
        veh = db.get(Vehicle, vid)
        self._recompute(db, veh)
        db.flush()
        events = [{"type": "vehicle.update", "payload": vehicle_dict(veh)}]
        if det.plate_text:
            events.append({"type": "plate_bound",
                           "payload": {"vehicle": vehicle_dict(veh), "detection_id": det.id,
                                       "plate_text": det.plate_text}})
        return events

    # --------------------------------------------------------------- union
    def _next_color(self) -> str:
        c = PALETTE[self._color_i % len(PALETTE)]
        self._color_i += 1
        return c

    def _union(self, db, signal_ids: list[int]) -> list[dict[str, Any]]:
        sigs = [s for s in (db.get(Signal, i) for i in signal_ids) if s is not None]
        if len(sigs) < 2:
            return []
        events: list[dict[str, Any]] = []
        existing_vids = {s.vehicle_id for s in sigs if s.vehicle_id}
        if not existing_vids:
            veh = Vehicle(color=self._next_color(),
                          first_seen=min(s.first_seen for s in sigs),
                          last_seen=max(s.last_seen for s in sigs))
            db.add(veh)
            db.flush()
            target, created = veh, True
        else:
            target = db.get(Vehicle, min(existing_vids))
            created = False

        removed: list[int] = []
        for s in sigs:
            if s.vehicle_id and s.vehicle_id != target.id:
                old = db.get(Vehicle, s.vehicle_id)
                if old:
                    for os_ in list(old.signals):
                        os_.vehicle_id = target.id
                    for od in list(old.detections):
                        od.vehicle_id = target.id
                    removed.append(old.id)
                    db.delete(old)
            s.vehicle_id = target.id
        db.flush()
        self._recompute(db, target)
        db.flush()
        events.append({"type": "vehicle.new" if created else "vehicle.update",
                       "payload": vehicle_dict(target)})
        for rid in removed:
            events.append({"type": "vehicle.removed", "payload": {"id": rid}})
        return events

    @staticmethod
    def _recompute(db, veh: Vehicle) -> None:
        signals = db.scalars(select(Signal).where(Signal.vehicle_id == veh.id)).all()
        detections = db.scalars(select(Detection).where(Detection.vehicle_id == veh.id)).all()
        if signals:
            veh.first_seen = min(s.first_seen for s in signals)
            veh.last_seen = max(s.last_seen for s in signals)
        persistent = [s for s in signals if s.category in ("tpms", "entertainment")]
        total_sightings = sum(s.count for s in signals)
        veh.score = (
            12 * len(persistent)
            + 4 * (len(signals) - len(persistent))
            + 30 * sum(1 for d in detections if d.plate_text)
            + 10 * len(detections)
            + min(20.0, math.log1p(total_sightings) * 4)
        )
