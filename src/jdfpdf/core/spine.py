"""Rückentitel: Text und Bild auf dem Buchrücken eines Umschlags."""

from __future__ import annotations

import math

from reportlab.lib.utils import ImageReader

from .layers import add_layer, cmyk, draw_overlay, font, remove_layers
from .media import Media
from .pdfdoc import PdfDocument

MM = 72 / 25.4
LAYER = "spine"


def spine_width_mm(page_count: int, media: Media, duplex: bool = True) -> float:
    """Rückenbreite = Anzahl Blätter × Papierdicke."""
    sheets = math.ceil(page_count / 2) if duplex else page_count
    return sheets * media.thickness_mm


def add_spine_text(
    doc: PdfDocument, cover_index: int, text: str, spine_width_mm: float, *,
    font_size: float | None = None, bold: bool = True, top_to_bottom: bool = True,
    color_cmyk: tuple[float, float, float, float] = (0, 0, 0, 100),
    background_cmyk: tuple[float, float, float, float] | None = None, image: str | None = None,
) -> None:
    """Rückentitel mittig auf einen Umschlag (Rückseite | Rücken | Vorderseite) setzen.

    ``top_to_bottom`` = Leserichtung von oben nach unten (üblich im deutschsprachigen Raum
    und im Englischen); sonst von unten nach oben.
    """
    mx0, my0, mx1, my1 = doc.box(cover_index, "MediaBox")
    tx0, ty0, tx1, ty1 = doc.box(cover_index, "TrimBox")
    spine = spine_width_mm * MM
    if spine <= 0 or spine > (tx1 - tx0):
        raise ValueError("Rückenbreite passt nicht zum Umschlag")
    cx = (tx0 + tx1) / 2 - mx0
    cy = (ty0 + ty1) / 2 - my0
    size = font_size or max(4.0, min(spine * 0.6, 24.0))

    def draw(c) -> None:
        if background_cmyk:
            c.setFillColor(cmyk(*background_cmyk))
            c.rect(cx - spine / 2, 0, spine, my1 - my0, stroke=0, fill=1)
        c.saveState()
        c.translate(cx, cy)
        c.rotate(-90 if top_to_bottom else 90)
        text_x = 0.0
        if image:
            reader = ImageReader(image)
            iw, ih = reader.getSize()
            img_h = spine * 0.8
            img_w = img_h * iw / ih
            length = (ty1 - ty0)
            c.drawImage(reader, -length / 2 + 10 * MM, -img_h / 2, img_w, img_h, mask="auto")
        c.setFillColor(cmyk(*color_cmyk))
        c.setFont(font(bold), size)
        c.drawCentredString(text_x, -size * 0.35, text)
        c.restoreState()

    add_layer(doc, cover_index, LAYER, draw_overlay(mx1 - mx0, my1 - my0, draw), (1, 0, 0, 1, mx0, my0))


def remove_spine(doc: PdfDocument, cover_index: int) -> int:
    return remove_layers(doc, cover_index, LAYER)
