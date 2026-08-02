"""Pydantic request bodies for mutating endpoints."""
from __future__ import annotations

from pydantic import BaseModel


class SignalPatch(BaseModel):
    label: str | None = None
    is_baseline: bool | None = None
    notes: str | None = None
    category: str | None = None


class VehiclePatch(BaseModel):
    label: str | None = None
    notes: str | None = None
    status: str | None = None


class BaselineLearn(BaseModel):
    # if set, only baseline signals last seen within this many minutes
    within_minutes: int | None = None


class VehicleSplit(BaseModel):
    signal_ids: list[int]           # members to pull out into a new vehicle
    label: str | None = None
    block_cross: bool = True        # block associations between the split groups


class VehicleDetach(BaseModel):
    signal_ids: list[int]           # members to detach (become uncorrelated)
    block: bool = True              # block associations to the remaining members


class SignalReassign(BaseModel):
    vehicle_id: int | None = None   # target vehicle id, or null to detach
    block: bool = True              # block associations to the previous vehicle
