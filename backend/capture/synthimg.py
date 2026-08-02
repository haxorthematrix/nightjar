"""Synthetic vehicle-capture image generator (mock mode only).

Produces a stylised "ALPR capture" JPEG whose rendered license plate matches the record's
recovered plate. Fully offline; depicts no real vehicle, plate, or person. When the real
webcam is attached, `camera.py` writes genuine frames instead and this is unused.
"""
from __future__ import annotations

import random
from datetime import datetime
from pathlib import Path

_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]


def available() -> bool:
    try:
        import PIL  # noqa: F401
        return True
    except Exception:
        return False


def _font(size: int):
    from PIL import ImageFont
    for p in _FONT_CANDIDATES:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def _hex(c: str) -> tuple[int, int, int]:
    c = (c or "#38bdf8").lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore


def _mix(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _centered(draw, box, text, font, fill):
    x0, y0, x1, y1 = box
    l, t, r, b = draw.textbbox((0, 0), text, font=font)
    draw.text((x0 + (x1 - x0 - (r - l)) / 2 - l, y0 + (y1 - y0 - (b - t)) / 2 - t),
              text, font=font, fill=fill)


def generate(path: str | Path, plate: str, color: str = "#38bdf8",
             ts: datetime | None = None, region: str = "",
             confidence: float | None = None, seed: str | None = None) -> None:
    from PIL import Image, ImageDraw

    rng = random.Random(seed or plate or "x")
    W, H = 720, 450
    body = _hex(color)
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img, "RGBA")

    # --- dusk sky gradient ---
    top, horizon = (18, 24, 38), _mix((60, 70, 95), body, 0.15)
    for y in range(int(H * 0.62)):
        d.line([(0, y), (W, y)], fill=_mix(top, horizon, y / (H * 0.62)))
    # --- road with perspective ---
    road = (26, 30, 38)
    d.rectangle([0, int(H * 0.62), W, H], fill=road)
    d.polygon([(W * 0.36, H * 0.62), (W * 0.64, H * 0.62), (W, H), (0, H)], fill=(34, 39, 48))
    for k in range(6):  # centre lane dashes receding
        yy = H * 0.66 + k * (H * 0.34 / 6)
        w = 3 + k * 3
        d.rectangle([W / 2 - w, yy, W / 2 + w, yy + 4 + k * 2], fill=(210, 200, 120, 160))

    # --- car ---
    cx = W * (0.5 + rng.uniform(-0.05, 0.05))
    by0, by1 = H * 0.44, H * 0.70          # body top/bottom
    bx0, bx1 = cx - W * 0.28, cx + W * 0.28
    d.ellipse([bx0 - 10, by1 - 6, bx1 + 10, by1 + 30], fill=(0, 0, 0, 90))  # shadow
    # cabin
    d.polygon([(cx - W * 0.15, by0), (cx + W * 0.15, by0),
               (cx + W * 0.20, by0 + H * 0.10), (cx - W * 0.20, by0 + H * 0.10)],
              fill=_mix(body, (0, 0, 0), 0.25))
    # windshield / windows
    glass = (150, 180, 205)
    d.polygon([(cx - W * 0.13, by0 + 6), (cx + W * 0.13, by0 + 6),
               (cx + W * 0.17, by0 + H * 0.09), (cx - W * 0.17, by0 + H * 0.09)], fill=glass)
    d.line([(cx, by0 + 6), (cx, by0 + H * 0.09)], fill=(40, 50, 60), width=3)
    # body
    d.rounded_rectangle([bx0, by0 + H * 0.09, bx1, by1], radius=26, fill=body)
    d.rounded_rectangle([bx0, by0 + H * 0.09, bx1, by1], radius=26,
                        outline=_mix(body, (255, 255, 255), 0.25), width=2)
    # highlight strip
    d.line([(bx0 + 14, by0 + H * 0.16), (bx1 - 14, by0 + H * 0.16)],
           fill=_mix(body, (255, 255, 255), 0.35), width=3)
    # wheels
    for wx in (cx - W * 0.18, cx + W * 0.18):
        d.ellipse([wx - 26, by1 - 20, wx + 26, by1 + 32], fill=(18, 18, 20))
        d.ellipse([wx - 11, by1 - 5, wx + 11, by1 + 17], fill=(120, 126, 134))
    # headlights with glow
    for hx in (bx0 + 16, bx1 - 16):
        for r, a in ((22, 40), (12, 90), (6, 200)):
            d.ellipse([hx - r, by0 + H * 0.15 - r + 12, hx + r, by0 + H * 0.15 + r + 12],
                      fill=(255, 240, 190, a))

    # --- license plate (matches the recovered plate) ---
    pw, ph = 190, 62
    px0, py0 = cx - pw / 2, by1 - ph - 6
    d.rounded_rectangle([px0, py0, px0 + pw, py0 + ph], radius=7,
                        fill=(245, 246, 235), outline=(18, 28, 40), width=3)
    _centered(d, (px0, py0 + 2, px0 + pw, py0 + ph - 12), (plate or "??????").upper(),
              _font(38), (20, 30, 46))
    if region:
        _centered(d, (px0, py0 + ph - 18, px0 + pw, py0 + ph - 2), region.upper(),
                  _font(11), (90, 100, 120))

    # --- ALPR bounding box + label ---
    d.rectangle([px0 - 6, py0 - 6, px0 + pw + 6, py0 + ph + 6], outline=(52, 211, 153), width=2)
    conf = f"  {int((confidence or 0.85) * 100)}%"
    d.text((px0 - 6, py0 - 24), f"PLATE{conf}", font=_font(14), fill=(52, 211, 153))

    # --- HUD overlays ---
    stamp = (ts or datetime.utcnow()).strftime("%Y-%m-%d %H:%M:%SZ")
    d.rectangle([0, 0, W, 26], fill=(0, 0, 0, 120))
    d.text((10, 6), f"CAM-01  {stamp}", font=_font(14), fill=(210, 220, 230))
    d.text((W - 205, 6), "◗ NIGHTJAR — SYNTHETIC", font=_font(13), fill=(120, 200, 235))
    d.rectangle([0, H - 22, W, H], fill=(0, 0, 0, 120))
    d.text((10, H - 18), "passive capture · demo imagery — not a real vehicle",
           font=_font(12), fill=(150, 160, 172))

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG", quality=86)


def make_capture_file(captures_dir: Path, root: Path, plate: str, color: str,
                      ts: datetime, region: str = "", confidence: float | None = None) -> str:
    """Generate an image under captures_dir; return its path relative to `root` (or '')."""
    if not available():
        return ""
    fname = captures_dir / f"cap_{ts.strftime('%Y%m%d_%H%M%S_%f')}.jpg"
    try:
        generate(fname, plate, color=color, ts=ts, region=region, confidence=confidence)
    except Exception:
        return ""
    return str(fname.relative_to(root))
