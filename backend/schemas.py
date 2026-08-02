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
