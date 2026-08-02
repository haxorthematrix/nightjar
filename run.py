#!/usr/bin/env python3
"""Nightjar entrypoint.  Usage: python run.py  (reads config.yaml / defaults)."""
from __future__ import annotations

import uvicorn

from backend.settings import get_settings


def main() -> None:
    s = get_settings()
    uvicorn.run(
        "backend.main:app",
        host=s["http"]["host"],
        port=int(s["http"]["port"]),
        log_level="info",
        reload=False,
    )


if __name__ == "__main__":
    main()
