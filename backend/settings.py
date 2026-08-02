"""Configuration loading.

Defaults live here; a `config.yaml` next to `config.example.yaml` (or a path in the
NIGHTJAR_CONFIG env var) is deep-merged on top. Kept intentionally simple (plain dict +
attribute access) so the whole thing runs with only pyyaml + pydantic installed.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent

DEFAULTS: dict[str, Any] = {
    "mock_mode": True,
    "http": {"host": "127.0.0.1", "port": 8008},
    "database": {"path": "data/nightjar.db"},
    "captures_dir": "data/captures",
    "services": {
        "simulator": {"enabled": True, "autostart": True},
        "tpms": {"enabled": True, "autostart": False},
        "ble": {"enabled": True, "autostart": False},
        "bt_classic": {"enabled": False, "autostart": False},
        "camera": {"enabled": True, "autostart": False},
    },
    "tpms": {
        "rtl_433_bin": "rtl_433",
        "frequencies": ["315M", "433.92M"],
        "hop_interval": 30,     # seconds between band hops (single dongle can't do both at once)
        "protocols": [],
        "extra_args": [],
    },
    "ble": {"adapter": "hci0", "active_scan": True, "presence_timeout": 90,
            "min_interval": 2.0},
    "bt_classic": {"adapter": "hci0", "inquiry_interval": 30},
    "camera": {
        "device": 0,
        "trigger": "rf_event",
        "interval": 5,
        "alpr_backend": "auto",
        "min_plate_confidence": 0.5,
    },
    "correlation": {
        "scene_window": 20,            # seconds; units seen within this share a "scene"
        "min_encounters": 3,           # co-occurrence episodes before a suggestion is raised
        "anchor_categories": ["tpms", "entertainment"],  # durable identifiers that seed vehicles
        "durability": {                # per-category weight (rotating phones are weak)
            "tpms": 1.0, "entertainment": 1.0,
            "phone": 0.2, "wearable": 0.2, "unknown": 0.5,
        },
        # RSSI-profile gate: same-vehicle units rise/fall together. Require their RSSI time
        # series to correlate before proposing (and rank proposals by it).
        "min_rssi_corr": 0.4,          # Pearson r threshold to allow a suggestion
        "min_rssi_samples": 6,         # paired samples before the gate is trusted
        "pair_window": 6,              # seconds; max gap for two readings to count as contemporaneous
        # Non-anchor (phone/wearable) attach requires proof of co-movement, not just
        # co-occurrence: correlated AND with real RSSI swing on both units. Rejects
        # stationary neighbour devices sitting near a parked vehicle.
        "attach_require_comovement": True,
        "min_rssi_std": 3.0,           # dB; min RSSI std-dev for an attaching device to count as "moving"
    },
    "baseline": {"allowlist": [], "warmup_minutes": 0},
    "retention": {"sighting_days": 30, "detection_days": 30},
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class Settings:
    """Dict-backed settings with dotted access, e.g. settings["tpms"]["frequencies"]."""

    def __init__(self, data: dict[str, Any]):
        self._d = data

    def __getitem__(self, key: str) -> Any:
        return self._d[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self._d.get(key, default)

    @property
    def data(self) -> dict[str, Any]:
        return self._d

    # convenience absolute paths
    @property
    def db_path(self) -> Path:
        return (ROOT / self._d["database"]["path"]).resolve()

    @property
    def captures_dir(self) -> Path:
        p = (ROOT / self._d["captures_dir"]).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p


@lru_cache
def get_settings() -> Settings:
    cfg_path = os.environ.get("NIGHTJAR_CONFIG")
    if cfg_path:
        path = Path(cfg_path)
    else:
        path = ROOT / "config.yaml"
    data = DEFAULTS
    if path.exists():
        with open(path) as fh:
            user = yaml.safe_load(fh) or {}
        data = _deep_merge(DEFAULTS, user)
    # env override for the single most common toggle
    if "NIGHTJAR_MOCK" in os.environ:
        data = _deep_merge(data, {"mock_mode": os.environ["NIGHTJAR_MOCK"] not in ("0", "false", "False")})
    return Settings(data)
