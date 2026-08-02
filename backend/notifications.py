"""Notification rule engine (see specification.md §6).

Rules turn pipeline events into persisted, streamable Notifications. Baseline/allowlisted
identifiers are suppressed so the operator's own devices don't spam alerts.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .database import session_scope
from .models import Notification
from .serialize import notification_dict

CATEGORY_LABEL = {
    "tpms": "TPMS sensor",
    "entertainment": "infotainment device",
    "phone": "phone/wearable",
    "wearable": "wearable",
    "unknown": "RF device",
}


class NotificationEngine:
    def __init__(self, bus, allowlist: list[str] | None = None):
        self.bus = bus
        self.allowlist = [a.lower() for a in (allowlist or [])]

    def _allowlisted(self, signal: dict[str, Any]) -> bool:
        hay = f"{signal.get('identifier','')} {signal.get('label','')}".lower()
        return any(a and a in hay for a in self.allowlist)

    async def _raise(self, *, level: str, rule: str, title: str, body: str,
                     signal_id: int | None = None, vehicle_id: int | None = None,
                     detection_id: int | None = None) -> None:
        with session_scope() as db:
            n = Notification(ts=datetime.now(timezone.utc), level=level, rule=rule,
                             title=title, body=body, signal_id=signal_id,
                             vehicle_id=vehicle_id, detection_id=detection_id)
            db.add(n)
            db.flush()
            payload = notification_dict(n)
        await self.bus.publish("notification.new", payload)

    # ---- rules -----------------------------------------------------------
    async def on_signal(self, signal: dict[str, Any], is_new: bool) -> None:
        if not is_new or signal.get("is_baseline") or self._allowlisted(signal):
            return
        cat = CATEGORY_LABEL.get(signal.get("category", "unknown"), "RF device")
        await self._raise(
            level="alert", rule="new_signal_over_baseline",
            title=f"New {cat} detected",
            body=f"{signal['kind'].upper()} {signal['identifier']} appeared over baseline.",
            signal_id=signal["id"],
        )

    async def on_vehicle(self, vehicle: dict[str, Any], created: bool) -> None:
        if not created:
            return
        n_id = vehicle.get("signal_count", 0)
        await self._raise(
            level="alert", rule="new_vehicle",
            title=f"New vehicle correlated ({n_id} identifiers)",
            body=(f"{vehicle.get('label')} formed from co-occurring identifiers — a "
                  f"re-identifiable RF fingerprint."),
            vehicle_id=vehicle["id"],
        )

    async def on_suggestion(self, sug: dict[str, Any]) -> None:
        a = sug.get("a") or {}
        b = sug.get("b") or {}
        verb = {"form": "Form vehicle from", "attach": "Attach unit to vehicle:",
                "merge": "Merge vehicles:"}.get(sug.get("kind"), "Correlate")
        await self._raise(
            level="alert", rule="correlation_suggested",
            title=f"Correlation suggested — {verb} {a.get('identifier','?')} ↔ {b.get('identifier','?')}",
            body=(f"{sug.get('rationale','')}  Confidence {int((sug.get('confidence',0))*100)}%. "
                  f"Review to confirm."),
        )

    async def on_plate_bound(self, vehicle: dict[str, Any], detection_id: int,
                             plate_text: str) -> None:
        await self._raise(
            level="critical", rule="plate_bound",
            title=f"Plate ↔ RF identity bound: {plate_text}",
            body=(f"License plate {plate_text} correlated to {vehicle.get('label')} "
                  f"({vehicle.get('signal_count', 0)} RF identifiers). Full de-anonymization."),
            vehicle_id=vehicle["id"], detection_id=detection_id,
        )
