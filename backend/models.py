"""ORM models. See specification.md §3."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .timeutil import now as utcnow  # naive UTC, consistent with DB round-trips


class Base(DeclarativeBase):
    pass


class Vehicle(Base):
    """A correlated physical entity — a cluster of signals (+ detections)."""

    __tablename__ = "vehicles"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(120), default="")
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    score: Mapped[float] = mapped_column(Float, default=0.0)   # notability / exposure score
    color: Mapped[str] = mapped_column(String(16), default="#38bdf8")
    status: Mapped[str] = mapped_column(String(24), default="active")  # active|archived
    notes: Mapped[str] = mapped_column(Text, default="")

    signals: Mapped[list["Signal"]] = relationship(back_populates="vehicle")
    detections: Mapped[list["Detection"]] = relationship(back_populates="vehicle")


class Signal(Base):
    """A unique RF identifier ever observed."""

    __tablename__ = "signals"
    __table_args__ = (UniqueConstraint("kind", "identifier", name="uq_kind_identifier"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(24), index=True)        # tpms|ble|bt_classic
    identifier: Mapped[str] = mapped_column(String(160), index=True)
    label: Mapped[str] = mapped_column(String(120), default="")
    category: Mapped[str] = mapped_column(String(32), default="unknown")  # tpms|entertainment|phone|wearable|unknown
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    rssi_last: Mapped[float | None] = mapped_column(Float, nullable=True)
    rssi_best: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_baseline: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    notes: Mapped[str] = mapped_column(Text, default="")

    vehicle_id: Mapped[int | None] = mapped_column(ForeignKey("vehicles.id"), nullable=True, index=True)
    vehicle: Mapped["Vehicle | None"] = relationship(back_populates="signals")
    sightings: Mapped[list["Sighting"]] = relationship(
        back_populates="signal", cascade="all, delete-orphan"
    )


class Sighting(Base):
    """One observation event for a Signal (the time series)."""

    __tablename__ = "sightings"

    id: Mapped[int] = mapped_column(primary_key=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    rssi: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="")
    data: Mapped[dict] = mapped_column(JSON, default=dict)

    signal: Mapped["Signal"] = relationship(back_populates="sightings")


class Detection(Base):
    """A camera capture, optionally with a recovered plate."""

    __tablename__ = "detections"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    image_path: Mapped[str] = mapped_column(String(255), default="")
    plate_text: Mapped[str | None] = mapped_column(String(32), nullable=True)
    plate_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    region: Mapped[str | None] = mapped_column(String(16), nullable=True)
    bbox: Mapped[dict] = mapped_column(JSON, default=dict)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)

    vehicle_id: Mapped[int | None] = mapped_column(ForeignKey("vehicles.id"), nullable=True, index=True)
    vehicle: Mapped["Vehicle | None"] = relationship(back_populates="detections")


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    level: Mapped[str] = mapped_column(String(16), default="info")  # info|alert|critical
    rule: Mapped[str] = mapped_column(String(48), default="")
    title: Mapped[str] = mapped_column(String(160), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id"), nullable=True)
    vehicle_id: Mapped[int | None] = mapped_column(ForeignKey("vehicles.id"), nullable=True)
    detection_id: Mapped[int | None] = mapped_column(ForeignKey("detections.id"), nullable=True)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class Association(Base):
    """Persistent co-occurrence evidence between two units (signals).

    This is the heart of 'correlate over time': every time two units are co-present in a
    scene across *separate* encounters, `co_count` grows. Stored canonically with
    a_id < b_id. `blocked` = operator rejected a merge for this pair (never re-suggest).
    """

    __tablename__ = "associations"
    __table_args__ = (UniqueConstraint("a_id", "b_id", name="uq_assoc_pair"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    a_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), index=True)
    b_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), index=True)
    co_count: Mapped[int] = mapped_column(Integer, default=0)   # distinct co-occurrence episodes
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    blocked: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # streaming Pearson accumulators over contemporaneous RSSI sample pairs (a=a_id, b=b_id)
    n_rssi: Mapped[int] = mapped_column(Integer, default=0)
    s_a: Mapped[float] = mapped_column(Float, default=0.0)
    s_b: Mapped[float] = mapped_column(Float, default=0.0)
    s_aa: Mapped[float] = mapped_column(Float, default=0.0)
    s_bb: Mapped[float] = mapped_column(Float, default=0.0)
    s_ab: Mapped[float] = mapped_column(Float, default=0.0)
    rssi_corr: Mapped[float | None] = mapped_column(Float, nullable=True)  # cached, None until enough samples


class Suggestion(Base):
    """A proposed correlation awaiting operator confirmation (human-in-the-loop)."""

    __tablename__ = "suggestions"

    id: Mapped[int] = mapped_column(primary_key=True)
    a_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), index=True)
    b_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), index=True)
    detection_id: Mapped[int | None] = mapped_column(ForeignKey("detections.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(24), default="form")  # form|attach|merge
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    encounters: Mapped[int] = mapped_column(Integer, default=0)
    rssi_corr: Mapped[float | None] = mapped_column(Float, nullable=True)
    rationale: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending|accepted|rejected
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ServiceState(Base):
    """Persisted runtime state of one capture service."""

    __tablename__ = "service_state"

    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    status: Mapped[str] = mapped_column(String(24), default="stopped")  # stopped|starting|running|error
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
