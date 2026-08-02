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

    def __init__(self):
        from fast_alpr import ALPR  # type: ignore

        self._alpr = ALPR(detector_model="yolo-v9-t-384-license-plate-end2end",
                          ocr_model="global-plates-mobile-vit-v2-model")

    def read(self, frame):  # noqa: ANN001
        results = self._alpr.predict(frame)
        best = None
        for r in results:
            ocr = getattr(r, "ocr", None)
            if not ocr or not ocr.text:
                continue
            conf = float(getattr(ocr, "confidence", 0.0))
            if best is None or conf > best.confidence:
                box = getattr(r, "detection", None)
                bbox = None
                if box is not None and getattr(box, "bounding_box", None) is not None:
                    b = box.bounding_box
                    bbox = {"x1": b.x1, "y1": b.y1, "x2": b.x2, "y2": b.y2}
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
