"""Pluggable ALPR backend.

`get_alpr(backend)` returns an object with `.read(frame_bgr) -> PlateResult | None`.
Backends:
  - 'fast_alpr' : uses the `fast-alpr` package (ONNX plate detector + OCR) if installed.
  - 'none'      : no OCR; frames are still stored so the operator can review manually.
  - 'auto'      : fast_alpr if importable, else none.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PlateResult:
    text: str
    confidence: float
    region: str | None = None
    bbox: dict | None = None


class NullALPR:
    backend = "none"

    def read(self, frame):  # noqa: ANN001
        return None


class FastALPR:
    backend = "fast_alpr"

    def __init__(self, **kwargs):
        from fast_alpr import ALPR  # type: ignore

        # Use the package defaults (detector + OCR models) so we track the installed version
        # rather than pinning model names that change between releases. Overridable via kwargs.
        self._alpr = ALPR(**kwargs)

    @staticmethod
    def _conf(value) -> float:
        # OcrResult.confidence may be a single float or one value per character
        if isinstance(value, (list, tuple)):
            return float(sum(value) / len(value)) if value else 0.0
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def read(self, frame):  # noqa: ANN001
        best = None
        for r in self._alpr.predict(frame):
            ocr = getattr(r, "ocr", None)
            if not ocr or not getattr(ocr, "text", None):
                continue
            conf = self._conf(ocr.confidence)
            if best is None or conf > best.confidence:
                bbox = None
                det = getattr(r, "detection", None)
                bb = getattr(det, "bounding_box", None) if det else None
                if bb is not None:
                    bbox = {"x1": int(bb.x1), "y1": int(bb.y1),
                            "x2": int(bb.x2), "y2": int(bb.y2)}
                best = PlateResult(text=ocr.text.strip().upper(), confidence=conf, bbox=bbox)
        return best


def get_alpr(backend: str = "auto"):
    if backend in ("auto", "fast_alpr"):
        try:
            return FastALPR()
        except Exception:  # noqa: BLE001
            if backend == "fast_alpr":
                raise
    return NullALPR()
