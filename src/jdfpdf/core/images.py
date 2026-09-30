"""Bilder (JPEG, PNG, TIFF, auch mehrseitig) in PDF-Seiten umwandeln."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageSequence


def images_to_pdf(paths: list[str | Path], default_dpi: float = 300) -> bytes:
    """Jedes Bild (jeder TIFF-Frame) wird eine Seite in Originalgröße laut DPI-Angabe."""
    frames: list[Image.Image] = []
    resolutions: list[float] = []
    for path in paths:
        with Image.open(path) as img:
            for frame in ImageSequence.Iterator(img):
                dpi = frame.info.get("dpi", (default_dpi, default_dpi))[0] or default_dpi
                converted = frame.convert("CMYK" if frame.mode == "CMYK" else "RGB") \
                    if frame.mode not in ("RGB", "L", "CMYK") else frame.copy()
                frames.append(converted)
                resolutions.append(float(dpi))
    if not frames:
        raise ValueError("Keine Bilder gefunden")
    # Pillow schreibt je Aufruf eine Auflösung; daher Seiten einzeln erzeugen und zusammenfügen.
    import pikepdf

    out = pikepdf.new()
    for frame, dpi in zip(frames, resolutions):
        buf = io.BytesIO()
        frame.save(buf, format="PDF", resolution=dpi)
        with pikepdf.open(io.BytesIO(buf.getvalue())) as single:
            out.pages.extend(single.pages)
    result = io.BytesIO()
    out.save(result)
    return result.getvalue()
